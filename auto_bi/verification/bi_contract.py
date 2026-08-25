"""Pure BI compile contracts (DECISIONOPS_ARCHITECTURE §8.5).

The production `BIAdapter` mixes two jobs: it *compiles* a `DashboardSpec` into a target's
native payloads, and it *creates* those entities remotely. A release gate needs the first
half and must never reach the second (invariant 2), so this module adds the compile-only
port and its two implementations.

What makes the contract worth anything is that there is no second payload implementation.
`SupersetContractCompiler` calls `build_form_data`, `build_position_json` and
`build_native_filter_configuration`; `DataLensContractCompiler` calls
`build_dataset_payload`, `build_chart_shared`, `build_selectors` and `build_dashboard_data`
— the same module-level functions `SupersetAdapter.build()` and `DataLensAdapter.build()`
call. A compiler that re-derived any of it could pass a payload production never builds.

Three things are deliberately *not* in a compiled contract:

* **Remote ids.** Datasets and charts get their ids from the BI on create. The contract
  uses deterministic placeholders (`native_id`) so the payloads stay self-consistent and two
  runs of the same input hash the same. Comparing contracts across a change is the point;
  replaying one against a particular stand is not.
* **The DWH connection.** `build_connection_payload` carries the DWH password. It is an
  environment binding, not a compile artifact, and §10.2 keeps it out of evidence.
* **The live-measured display scale.** `_ru_scale` measures a measure's magnitude against a
  running stand to choose "млрд" over the d3 SI form. It has no offline value, so the
  compiled KPI/axis payload omits it — declared as `live_measured_display_scale=False`
  rather than left to be discovered (invariant 6: an unrepresentable input is a stated gap).

Payloads are excluded from the model dump: the contract carries content addresses, and the
bytes travel to the evidence store as blobs (§9.1). A digest is checked against its payload
whenever one is present, so an in-memory contract cannot lie about what it compiled, and a
contract read back from evidence still verifies its own body.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from auto_bi.adapters.artifacts import dataset_table_name
from auto_bi.adapters.datalens.adapter import (
    build_dashboard_data,
    build_selectors,
    selector_scope_chart_ids,
)
from auto_bi.adapters.datalens.chart_config import DEGRADED, VIZ_ID, build_chart_shared
from auto_bi.adapters.datalens.dataset import build_dataset_payload, dataset_name, safe_entry_name
from auto_bi.adapters.superset.derive import heatmap_y_pad, metric_labels, temporal_alias
from auto_bi.adapters.superset.form_data import VIZ_TYPE, build_form_data, build_position_json
from auto_bi.adapters.superset.native_filters import (
    build_native_filter_configuration,
    participating_chart_ids,
)
from auto_bi.agent.dataset_plan import DatasetRole, plan_datasets, source_dataset_inputs
from auto_bi.agent.normalize import is_horizontal_bar
from auto_bi.agent.sqlgen import generate_chart_sql, generate_source_sql
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.ir.spec import ChartSpec, DashboardSpec, TargetBI, Viz
from auto_bi.scalars import NonBlankText, Sha256Digest, Slug
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"

#: Bumped whenever a compiled payload's shape changes, because that changes every digest and
#: therefore the identity of every contract — the same reason `NATIVE_SOURCE_VERSION` exists.
COMPILER_VERSION = "1"

#: The stand each target's payload shape was reverse-engineered and live-verified against.
#: Superset is pinned by digest in `docker/superset/Dockerfile`; the DataLens self-hosted
#: stand is pinned in the internal runbook (CLAUDE.md invariant 7).
SUPERSET_PINNED_VERSION = "4.1.2"
DATALENS_PINNED_VERSION = "gateway-4.10.4"

#: Placeholder ids for the DataLens workbook and connection the payloads reference. Both are
#: environment, not compile: fixing them keeps the contract deterministic and comparable.
_DATALENS_WORKBOOK_ID = "contract-workbook"
_DATALENS_CONNECTION_ID = "contract-connection"


class BIArtifactKind(StrEnum):
    """What a compiled artifact is on the target."""

    DATASET = "dataset"
    CHART = "chart"
    DASHBOARD = "dashboard"


class CapabilityLevel(StrEnum):
    SUPPORTED = "supported"
    #: Compiles, but not to what the IR asked for (DataLens renders a heatmap as a pivot).
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class TargetCapabilities(StrictModel):
    """What this target can express, as the compiler knows it — part of the digest.

    The visualization map is read from the adapter's own pinned table rather than restated
    here: a viz the adapter cannot name is a viz the target cannot render.
    """

    target: TargetBI
    target_version: NonBlankText
    compiler_version: NonBlankText
    visualizations: dict[str, CapabilityLevel]
    #: Whether the target compiles dashboard-level controls (Superset native filters,
    #: DataLens selectors) rather than baking every filter into chart SQL.
    dashboard_filters: bool
    #: False everywhere today: the RU magnitude scale is measured against a running stand,
    #: so an offline contract cannot carry it (see the module docstring).
    live_measured_display_scale: bool = False

    def level(self, viz: Viz) -> CapabilityLevel:
        return self.visualizations.get(viz.value, CapabilityLevel.UNSUPPORTED)


class CompiledArtifact(StrictModel):
    """One native payload the adapter would create, addressed by content."""

    artifact_id: Slug
    kind: BIArtifactKind
    #: The entity as the spec names it: chart id, dataset name, dashboard title.
    subject: NonBlankText
    #: Deterministic placeholder for the id the BI would assign; referenced by the payloads.
    native_id: Slug
    digest: Sha256Digest
    #: Excluded from the dump — the bytes belong in the evidence store, the address here.
    payload: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    @model_validator(mode="after")
    def _check_digest(self) -> CompiledArtifact:
        if self.payload and self.digest != payload_digest(self.payload):
            raise ValueError(f"digest does not address the payload of {self.artifact_id!r}")
        return self


class ContractFinding(StrictModel):
    """Something true about compiling *this* dashboard for *this* target."""

    code: NonBlankText
    severity: Severity
    subject: NonBlankText
    detail: NonBlankText


class CompiledBIContract(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="CompiledBIContract", pattern=r"^CompiledBIContract$")
    capabilities: TargetCapabilities
    spec_digest: Sha256Digest
    artifacts: list[CompiledArtifact] = Field(default_factory=list)
    findings: list[ContractFinding] = Field(default_factory=list)
    contract_digest: Sha256Digest

    @model_validator(mode="after")
    def _check_digest(self) -> CompiledBIContract:
        expected = body_digest(self.capabilities, self.spec_digest, self.artifacts, self.findings)
        if self.contract_digest != expected:
            raise ValueError(
                f"contract_digest does not address this contract (expected {expected})"
            )
        return self

    def artifact(self, artifact_id: str) -> CompiledArtifact | None:
        return next((item for item in self.artifacts if item.artifact_id == artifact_id), None)

    def severities(self) -> list[Severity]:
        return [finding.severity for finding in self.findings]


class BIContractCompiler(Protocol):
    """Compile-only port (§8.5). No create, no cleanup, no client.

    `TargetCapabilities` is returned by the compiler rather than passed in as §8.5 first
    sketched: they are read from the adapter's pinned payload tables, and a caller able to
    supply them could declare support the compiler does not have.
    """

    target: TargetBI

    def capabilities(self) -> TargetCapabilities: ...

    def compile_contract(self, spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract: ...


def payload_digest(payload: dict[str, Any]) -> str:
    """Content address of a native BI payload.

    `allow_float=True` because these are foreign JSON documents: Superset `form_data` and
    DataLens configs carry IEEE-754 numbers, for which RFC 8785 defines a serialization. The
    strict contract layer (decimal strings only) governs *our* contracts, not theirs.
    """
    return sha256_digest(payload, allow_float=True)


def body_digest(
    capabilities: TargetCapabilities,
    spec_digest: str,
    artifacts: Sequence[CompiledArtifact],
    findings: Sequence[ContractFinding],
) -> str:
    """Content address of everything a contract asserts.

    `api_version`/`kind` are pinned by pattern and cannot vary, so they carry no identity;
    `contract_digest` itself is excluded for the obvious reason.
    """
    return sha256_digest(
        {
            "capabilities": capabilities.model_dump(mode="json"),
            "spec_digest": spec_digest,
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "findings": [item.model_dump(mode="json") for item in findings],
        }
    )


def _finalize(
    capabilities: TargetCapabilities,
    spec: DashboardSpec,
    artifacts: list[CompiledArtifact],
    findings: list[ContractFinding],
) -> CompiledBIContract:
    spec_digest = sha256_digest(spec.model_dump(mode="json"), allow_float=True)
    return CompiledBIContract(
        capabilities=capabilities,
        spec_digest=spec_digest,
        artifacts=artifacts,
        findings=findings,
        contract_digest=body_digest(capabilities, spec_digest, artifacts, findings),
    )


def _viz_findings(
    spec: DashboardSpec,
    capabilities: TargetCapabilities,
    notes: Mapping[Viz, str] | None = None,
) -> list[ContractFinding]:
    notes = notes or {}
    findings: list[ContractFinding] = []
    for chart in spec.charts:
        level = capabilities.level(chart.viz)
        if level is CapabilityLevel.SUPPORTED:
            continue
        degraded = level is CapabilityLevel.DEGRADED
        findings.append(
            ContractFinding(
                code="bi.viz_degraded" if degraded else "bi.viz_unsupported",
                severity=Severity.ADVISORY if degraded else Severity.BLOCK,
                subject=chart.id,
                detail=notes.get(chart.viz)
                or f"{capabilities.target.value} has no payload for viz {chart.viz.value!r}",
            )
        )
    return findings


def _filter_findings(
    spec: DashboardSpec, wired: Mapping[str, tuple[list[str], list[str]]]
) -> list[ContractFinding]:
    """Turn the adapters' build-time warnings into typed findings.

    `wired` maps a filter column to (charts the control moves, charts it does not). A filter
    absent from the map reaches nothing at all — the control would render and do nothing,
    exactly the kind of silent half-truth a gate exists to name.
    """
    findings: list[ContractFinding] = []
    for filter_ in spec.filters:
        entry = wired.get(filter_.column)
        if entry is None:
            findings.append(
                ContractFinding(
                    code="bi.filter_unwired",
                    severity=Severity.ADVISORY,
                    subject=filter_.column,
                    detail=(
                        "no chart's dataset exposes this column, so the dashboard control is "
                        "not compiled; the baked query filters still constrain the data"
                    ),
                )
            )
            continue
        scoped, excluded = entry
        if excluded:
            findings.append(
                ContractFinding(
                    code="bi.filter_partial_scope",
                    severity=Severity.ADVISORY,
                    subject=filter_.column,
                    detail=(
                        f"the control moves {', '.join(scoped)} and does not reach "
                        f"{', '.join(excluded)}"
                    ),
                )
            )
    return findings


class SupersetContractCompiler:
    """Superset compile contract: virtual datasets, chart `params`, dashboard position.

    Mirrors `SupersetAdapter.build` step for step — the D-1 dataset plan, one shared source
    dataset per mart, the top-N LIMIT that moves into `form_data` for charts a native filter
    re-ranks — so a difference between the contract and the built dashboard can only come
    from the stand, never from this module.
    """

    target = TargetBI.SUPERSET

    def __init__(self, *, target_version: str = SUPERSET_PINNED_VERSION) -> None:
        self._target_version = target_version

    def capabilities(self) -> TargetCapabilities:
        return TargetCapabilities(
            target=self.target,
            target_version=self._target_version,
            compiler_version=COMPILER_VERSION,
            visualizations={
                viz.value: (
                    CapabilityLevel.SUPPORTED if viz in VIZ_TYPE else CapabilityLevel.UNSUPPORTED
                )
                for viz in Viz
            },
            dashboard_filters=True,
        )

    def compile_contract(self, spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract:
        capabilities = self.capabilities()
        datasets: list[CompiledArtifact] = []
        plan = plan_datasets(spec)
        in_filter_scope = participating_chart_ids(spec, model)

        # Datasets first, in `build()` order: one shared source dataset per mart, then the
        # per-chart aggregated dataset for every chart that cannot render off the source.
        source_ids: dict[str, int] = {}
        next_dataset_id = 1
        for table in plan.source_tables:
            inputs = source_dataset_inputs(spec, plan, model, table)
            sql = generate_source_sql(
                inputs.table, list(inputs.columns), list(inputs.joins), inputs.joined_refs
            )
            source_ids[table] = next_dataset_id
            datasets.append(
                _superset_dataset(
                    next_dataset_id, dataset_table_name(spec.title, f"source:{table}", ""), sql
                )
            )
            next_dataset_id += 1

        placements: list[tuple[ChartSpec, int, int]] = []
        charts: list[CompiledArtifact] = []
        for index, chart in enumerate(spec.charts, start=1):
            if plan.chart(chart.id).role is DatasetRole.SOURCE:
                dataset_id = source_ids[chart.query.table]
                from_source = True
            else:
                sql = generate_chart_sql(chart.query, apply_limit=chart.id not in in_filter_scope)
                dataset_id = next_dataset_id
                datasets.append(
                    _superset_dataset(dataset_id, dataset_table_name(spec.title, chart.id, ""), sql)
                )
                next_dataset_id += 1
                from_source = False

            payload = {
                "slice_name": chart.title,
                "viz_type": VIZ_TYPE[chart.viz],
                "datasource_id": dataset_id,
                "datasource_type": "table",
                "params": build_form_data(
                    chart,
                    dataset_id,
                    horizontal=is_horizontal_bar(chart, model),
                    metric_labels=metric_labels(chart, model),
                    time_column=temporal_alias(chart.query, model, from_source=from_source),
                    heatmap_y_pad=heatmap_y_pad(chart, model),
                    from_source=from_source,
                ),
            }
            charts.append(
                _artifact(f"chart-{index}", BIArtifactKind.CHART, chart.id, str(index), payload)
            )
            placements.append((chart, index, dataset_id))

        native_filters: list[dict[str, Any]] = []
        wired: dict[str, tuple[list[str], list[str]]] = {}
        if spec.filters:
            native_filters, applied = build_native_filter_configuration(
                spec, placements, model, plan=plan
            )
            by_slice = {slice_id: chart.id for chart, slice_id, _ in placements}
            for filter_, in_scope, excluded in applied:
                wired[filter_.column] = (
                    [by_slice[sid] for sid in in_scope],
                    [by_slice[sid] for sid in excluded],
                )

        json_metadata: dict[str, Any] = {"chart_configuration": {}}
        if native_filters:
            json_metadata["native_filter_configuration"] = native_filters
        dashboard_payload = {
            "dashboard_title": spec.title,
            # Structured, not the adapter's `json.dumps` string: a serializer's key order
            # must not be able to change a content address.
            "position_json": build_position_json(
                spec, [(chart, slice_id) for chart, slice_id, _ in placements]
            ),
            "json_metadata": json_metadata,
            "published": True,
        }
        artifacts = [
            *datasets,
            *charts,
            _artifact("dashboard", BIArtifactKind.DASHBOARD, spec.title, "1", dashboard_payload),
        ]
        findings = _viz_findings(spec, capabilities) + _filter_findings(spec, wired)
        return _finalize(capabilities, spec, artifacts, findings)


class DataLensContractCompiler:
    """DataLens compile contract: one dataset per chart, wizard `shared`, dash `data`.

    Mirrors `DataLensAdapter.build` minus the `__wip`/promote choreography, which is
    remote-mutation ordering and has no compiled form.
    """

    target = TargetBI.DATALENS

    def __init__(self, *, target_version: str = DATALENS_PINNED_VERSION) -> None:
        self._target_version = target_version

    def capabilities(self) -> TargetCapabilities:
        return TargetCapabilities(
            target=self.target,
            target_version=self._target_version,
            compiler_version=COMPILER_VERSION,
            visualizations={viz.value: _datalens_level(viz) for viz in Viz},
            dashboard_filters=True,
        )

    def compile_contract(self, spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract:
        capabilities = self.capabilities()
        datasets: list[CompiledArtifact] = []
        charts: list[CompiledArtifact] = []
        in_filter_scope = selector_scope_chart_ids(spec)
        placements: list[tuple[ChartSpec, str, str]] = []
        fields_by_dataset: dict[str, dict[str, dict[str, Any]]] = {}

        for index, chart in enumerate(spec.charts, start=1):
            dataset_id = f"dataset-{index}"
            ds_name = dataset_name(spec.title, chart.id, "")
            dataset_payload = build_dataset_payload(
                chart.query,
                model,
                workbook_id=_DATALENS_WORKBOOK_ID,
                connection_id=_DATALENS_CONNECTION_ID,
                name=ds_name,
                apply_limit=chart.id not in in_filter_scope,
            )
            datasets.append(
                _artifact(dataset_id, BIArtifactKind.DATASET, ds_name, dataset_id, dataset_payload)
            )
            # Keyed by the bare SQL alias, exactly as `ensure_dataset` keys the created
            # dataset's result_schema — the chart binds to these guids.
            fields = {f["source"]: f for f in dataset_payload["dataset"]["result_schema"]}
            fields_by_dataset[dataset_id] = fields

            widget_id = f"chart-{index}"
            payload = {
                "data": build_chart_shared(
                    chart,
                    dataset_id,
                    ds_name,
                    fields,
                    horizontal=is_horizontal_bar(chart, model),
                ),
                "template": "datalens",
                "workbookId": _DATALENS_WORKBOOK_ID,
                "name": safe_entry_name(chart.title),
            }
            charts.append(_artifact(widget_id, BIArtifactKind.CHART, chart.id, widget_id, payload))
            placements.append((chart, widget_id, dataset_id))

        controls: list[dict[str, Any]] = []
        alias_groups: list[list[str]] = []
        wired: dict[str, tuple[list[str], list[str]]] = {}
        if spec.filters:
            controls, alias_groups, applied = build_selectors(
                spec, placements, fields_by_dataset, model
            )
            for filter_, scoped, excluded in applied:
                wired[filter_.column] = (list(scoped), list(excluded))

        dashboard_payload = build_dashboard_data(
            spec, [widget_id for _, widget_id, _ in placements], controls, alias_groups
        )
        artifacts = [
            *datasets,
            *charts,
            _artifact(
                "dashboard",
                BIArtifactKind.DASHBOARD,
                spec.title,
                "dashboard-1",
                dashboard_payload,
            ),
        ]
        findings = _viz_findings(spec, capabilities, DEGRADED) + _filter_findings(spec, wired)
        return _finalize(capabilities, spec, artifacts, findings)


def _artifact(
    artifact_id: str,
    kind: BIArtifactKind,
    subject: str,
    native_id: str,
    payload: dict[str, Any],
) -> CompiledArtifact:
    return CompiledArtifact(
        artifact_id=artifact_id,
        kind=kind,
        subject=subject,
        native_id=native_id,
        digest=payload_digest(payload),
        payload=payload,
    )


def _superset_dataset(dataset_id: int, name: str, sql: str) -> CompiledArtifact:
    """A Superset virtual dataset. `database`/`schema` are environment bindings, not compile."""
    return _artifact(
        f"dataset-{dataset_id}",
        BIArtifactKind.DATASET,
        name,
        str(dataset_id),
        {"table_name": name, "sql": sql},
    )


def _datalens_level(viz: Viz) -> CapabilityLevel:
    if viz in DEGRADED:
        return CapabilityLevel.DEGRADED
    return CapabilityLevel.SUPPORTED if viz in VIZ_ID else CapabilityLevel.UNSUPPORTED
