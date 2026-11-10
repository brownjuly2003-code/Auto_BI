"""Pure BI compile contracts (DECISIONOPS_ARCHITECTURE §8.5, bootstrap task 7).

Two properties are load-bearing and both are pinned here:

* the compiler reaches the target through the *same* pure builders the production
  `BIAdapter.build()` uses — a second payload implementation would let the gate pass a
  dashboard the adapter builds differently;
* compiling touches nothing remote — no socket, no client method, no create.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_bi.adapters.datalens import adapter as datalens_adapter
from auto_bi.adapters.datalens.client import DataLensClient
from auto_bi.adapters.superset import adapter as superset_adapter
from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.adapters.superset.derive import metric_labels, temporal_alias
from auto_bi.adapters.superset.form_data import VIZ_TYPE, build_form_data
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    Measure,
    MeasureTransform,
    TargetBI,
    Viz,
)
from auto_bi.semantic.model import Aggregation, SemanticModel
from auto_bi.severity import Severity
from auto_bi.verification.bi_contract import (
    SUPERSET_PINNED_VERSION,
    BIArtifactKind,
    CapabilityLevel,
    CompiledArtifact,
    CompiledBIContract,
    DataLensContractCompiler,
    SupersetContractCompiler,
)

REVENUE = Measure(column="revenue", agg=Aggregation.SUM)
ORDERS = Measure(column="orders", agg=Aggregation.SUM)


def _kpi() -> ChartSpec:
    return ChartSpec(
        id="kpi_revenue",
        title="Выручка",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
    )


def _line() -> ChartSpec:
    return ChartSpec(
        id="revenue_by_day",
        title="Выручка по дням",
        viz=Viz.LINE,
        query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
    )


def _bar() -> ChartSpec:
    return ChartSpec(
        id="orders_by_store",
        title="Заказы по магазинам",
        viz=Viz.BAR,
        query=ChartQuery(table="dm.sales_daily", dimensions=["store_id"], measures=[ORDERS]),
    )


def _own_bar() -> ChartSpec:
    """A window transform is computed in SQL, so this chart keeps its own dataset."""
    return _bar().model_copy(
        update={
            "id": "share_by_store",
            "query": ChartQuery(
                table="dm.sales_daily",
                dimensions=["store_id"],
                measures=[
                    Measure(
                        column="revenue",
                        agg=Aggregation.SUM,
                        transform=MeasureTransform.SHARE_OF_TOTAL,
                    )
                ],
            ),
        }
    )


def make_spec(target: TargetBI = TargetBI.SUPERSET, **kwargs: Any) -> DashboardSpec:
    return DashboardSpec(
        title="Продажи",
        target_bi=target,
        charts=kwargs.pop("charts", [_kpi(), _line(), _bar()]),
        filters=kwargs.pop("filters", [DashboardFilter(column="dm.sales_daily.date")]),
        **kwargs,
    )


def artifacts_of(contract: CompiledBIContract, kind: BIArtifactKind) -> list[CompiledArtifact]:
    return [item for item in contract.artifacts if item.kind is kind]


def codes(contract: CompiledBIContract) -> list[str]:
    return [finding.code for finding in contract.findings]


# --- one implementation, two callers ------------------------------------------


def test_superset_compiler_and_adapter_share_one_form_data_builder() -> None:
    """§8.5: duplicating payload logic is forbidden — these must be the same object."""
    from auto_bi.verification import bi_contract

    assert bi_contract.build_form_data is superset_adapter.build_form_data
    assert bi_contract.build_position_json is superset_adapter.build_position_json
    assert (
        bi_contract.build_native_filter_configuration
        is superset_adapter.build_native_filter_configuration
    )
    assert bi_contract.generate_source_sql is superset_adapter.generate_source_sql


def test_datalens_compiler_and_adapter_share_one_payload_builder() -> None:
    from auto_bi.verification import bi_contract

    assert bi_contract.build_dataset_payload is datalens_adapter.build_dataset_payload
    assert bi_contract.build_chart_shared is datalens_adapter.build_chart_shared
    assert bi_contract.build_dashboard_data is datalens_adapter.build_dashboard_data
    assert bi_contract.build_selectors is datalens_adapter.build_selectors


def test_superset_chart_payload_equals_a_direct_pure_build(demo_model: SemanticModel) -> None:
    """The compiled chart is what `build_form_data` returns for the same derived inputs."""
    spec = make_spec(charts=[_line()], filters=[])
    contract = SupersetContractCompiler().compile_contract(spec, demo_model)
    chart = artifacts_of(contract, BIArtifactKind.CHART)[0]
    dataset = artifacts_of(contract, BIArtifactKind.DATASET)[0]

    expected = build_form_data(
        _line(),
        int(dataset.native_id),
        horizontal=False,
        metric_labels=metric_labels(_line(), demo_model),
        time_column=temporal_alias(_line().query, demo_model, from_source=True),
        from_source=True,
    )
    assert chart.payload["params"] == expected
    assert chart.payload["viz_type"] == VIZ_TYPE[Viz.LINE]


# --- no remote mutation -------------------------------------------------------


@pytest.mark.parametrize("target", [TargetBI.SUPERSET, TargetBI.DATALENS])
def test_compile_never_opens_a_socket(
    target: TargetBI, demo_model: SemanticModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the release gate must not reach the BI")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    compiler: Any = (
        SupersetContractCompiler() if target is TargetBI.SUPERSET else DataLensContractCompiler()
    )
    assert compiler.compile_contract(make_spec(target), demo_model).artifacts


def test_compile_calls_no_client_method(
    demo_model: SemanticModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation spy: every remote verb of both clients is armed to fail the test."""
    calls: list[str] = []

    def spy(name: str) -> Any:
        def fail(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"release gate called {name}")

        return fail

    for verb in ("request", "get", "post", "put", "delete", "login"):
        monkeypatch.setattr(SupersetClient, verb, spy(f"superset.{verb}"))
    for verb in ("post", "gateway", "login"):
        monkeypatch.setattr(DataLensClient, verb, spy(f"datalens.{verb}"))

    SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    DataLensContractCompiler().compile_contract(make_spec(TargetBI.DATALENS), demo_model)
    assert calls == []


def test_compilers_expose_no_mutation_verb() -> None:
    """A compiler is not an adapter: no build/create/ensure/delete surface exists to call."""
    for compiler in (SupersetContractCompiler(), DataLensContractCompiler()):
        public = {name for name in dir(compiler) if not name.startswith("_")}
        assert public == {"capabilities", "compile_contract", "target"}


# --- determinism and self-addressing ------------------------------------------


def test_contract_digest_is_deterministic(demo_model: SemanticModel) -> None:
    first = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    second = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    assert first.contract_digest == second.contract_digest
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_contract_digest_addresses_its_own_body(demo_model: SemanticModel) -> None:
    contract = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    data = contract.model_dump(mode="json")
    data["artifacts"][0]["subject"] = "something else"
    with pytest.raises(ValueError, match="contract_digest"):
        CompiledBIContract.model_validate(data)


def test_artifact_digest_addresses_its_payload(demo_model: SemanticModel) -> None:
    contract = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    artifact = artifacts_of(contract, BIArtifactKind.CHART)[0]
    data = artifact.model_dump(mode="json")
    data["payload"] = artifact.payload | {"slice_name": "another title"}
    with pytest.raises(ValueError, match="digest"):
        CompiledArtifact.model_validate(data)


def test_contract_round_trips_without_the_payloads(demo_model: SemanticModel) -> None:
    """Payloads travel as evidence blobs; the contract itself carries content addresses."""
    contract = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    dumped = contract.model_dump(mode="json")
    assert "payload" not in dumped["artifacts"][0]
    again = CompiledBIContract.model_validate(dumped)
    assert again.contract_digest == contract.contract_digest
    assert [item.digest for item in again.artifacts] == [item.digest for item in contract.artifacts]


def test_artifact_ids_are_unique_and_subjects_name_the_real_entity(
    demo_model: SemanticModel,
) -> None:
    contract = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    ids = [item.artifact_id for item in contract.artifacts]
    assert len(ids) == len(set(ids))
    assert {item.subject for item in artifacts_of(contract, BIArtifactKind.CHART)} == {
        "kpi_revenue",
        "revenue_by_day",
        "orders_by_store",
    }
    assert artifacts_of(contract, BIArtifactKind.DASHBOARD)[0].subject == "Продажи"


# --- what the compile contract actually says ----------------------------------


def test_presentation_only_change_keeps_every_dataset_digest(demo_model: SemanticModel) -> None:
    """A retitled chart changes the chart payload and nothing about the data it reads."""
    before = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    renamed = _line().model_copy(update={"title": "Динамика выручки"})
    after = SupersetContractCompiler().compile_contract(
        make_spec(charts=[_kpi(), renamed, _bar()]), demo_model
    )

    def by_subject(contract: CompiledBIContract, kind: BIArtifactKind) -> dict[str, str]:
        return {item.subject: item.digest for item in artifacts_of(contract, kind)}

    assert by_subject(before, BIArtifactKind.DATASET) == by_subject(after, BIArtifactKind.DATASET)
    assert by_subject(before, BIArtifactKind.CHART) != by_subject(after, BIArtifactKind.CHART)
    assert before.contract_digest != after.contract_digest


def test_datalens_heatmap_is_reported_as_a_degradation(demo_model: SemanticModel) -> None:
    heatmap = ChartSpec(
        id="cohort",
        title="Когорты",
        viz=Viz.HEATMAP,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date", "store_id"],
            measures=[REVENUE],
        ),
    )
    spec = make_spec(TargetBI.DATALENS, charts=[heatmap], filters=[])
    contract = DataLensContractCompiler().compile_contract(spec, demo_model)
    degraded = [item for item in contract.findings if item.code == "bi.viz_degraded"]
    assert [item.subject for item in degraded] == ["cohort"]
    assert degraded[0].severity is Severity.ADVISORY
    assert "pivot" in degraded[0].detail

    # the same chart is a first-class Superset heatmap: no degradation there
    superset = SupersetContractCompiler().compile_contract(
        make_spec(charts=[heatmap], filters=[]), demo_model
    )
    assert "bi.viz_degraded" not in codes(superset)


def test_filter_no_chart_can_honor_is_reported(demo_model: SemanticModel) -> None:
    """Superset logs this as a warning at build time; the gate needs it as a finding."""
    spec = make_spec(
        charts=[_bar()],
        filters=[DashboardFilter(column="dm.stores.city", type="select")],
    )
    contract = SupersetContractCompiler().compile_contract(spec, demo_model)
    unwired = [item for item in contract.findings if item.code == "bi.filter_unwired"]
    assert [item.subject for item in unwired] == ["dm.stores.city"]
    assert unwired[0].severity is Severity.ADVISORY


def test_partial_filter_scope_is_reported(demo_model: SemanticModel) -> None:
    """A control that moves some charts and not others is a real, reportable asymmetry."""
    spec = make_spec(
        charts=[_line(), _own_bar()],
        filters=[DashboardFilter(column="dm.sales_daily.date")],
    )
    contract = SupersetContractCompiler().compile_contract(spec, demo_model)
    partial = [item for item in contract.findings if item.code == "bi.filter_partial_scope"]
    assert [item.subject for item in partial] == ["dm.sales_daily.date"]
    assert "share_by_store" in partial[0].detail


def test_contract_carries_no_findings_for_a_plain_dashboard(demo_model: SemanticModel) -> None:
    spec = make_spec(charts=[_bar()], filters=[])
    assert SupersetContractCompiler().compile_contract(spec, demo_model).findings == []


# --- capabilities -------------------------------------------------------------


def test_capabilities_cover_every_visualization() -> None:
    """A new Viz must be classified for both targets, not silently compile to nothing."""
    for compiler in (SupersetContractCompiler(), DataLensContractCompiler()):
        levels = compiler.capabilities().visualizations
        assert {viz.value for viz in Viz} == set(levels)
    datalens = DataLensContractCompiler().capabilities()
    assert datalens.visualizations[Viz.HEATMAP.value] is CapabilityLevel.DEGRADED
    superset = SupersetContractCompiler().capabilities()
    assert superset.visualizations[Viz.HEATMAP.value] is CapabilityLevel.SUPPORTED


def test_capabilities_declare_the_live_measured_display_scale_gap() -> None:
    """Invariant 6: the RU magnitude divisor is measured against the stand, so the offline
    payload cannot carry it. The contract states that instead of implying byte equality."""
    for compiler in (SupersetContractCompiler(), DataLensContractCompiler()):
        assert compiler.capabilities().live_measured_display_scale is False


def test_capabilities_are_part_of_the_contract_digest(demo_model: SemanticModel) -> None:
    """The same dashboard verified against another stand version is another contract."""
    default = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    other = SupersetContractCompiler(target_version="5.0.0").compile_contract(
        make_spec(), demo_model
    )
    assert default.contract_digest != other.contract_digest
    assert other.capabilities.target_version == "5.0.0"


def test_pinned_superset_version_matches_the_image(demo_model: SemanticModel) -> None:
    """The pinned default is the stand the payload shape was reverse-engineered from."""
    dockerfile = Path("docker/superset/Dockerfile").read_text(encoding="utf-8")
    assert f"apache/superset:{SUPERSET_PINNED_VERSION}@sha256:" in dockerfile
    contract = SupersetContractCompiler().compile_contract(make_spec(), demo_model)
    assert contract.capabilities.target_version == SUPERSET_PINNED_VERSION
    assert contract.capabilities.target is TargetBI.SUPERSET


# --- DataLens shape -----------------------------------------------------------


def test_datalens_contract_binds_charts_to_their_compiled_datasets(
    demo_model: SemanticModel,
) -> None:
    contract = DataLensContractCompiler().compile_contract(make_spec(TargetBI.DATALENS), demo_model)
    datasets = artifacts_of(contract, BIArtifactKind.DATASET)
    charts = artifacts_of(contract, BIArtifactKind.CHART)
    assert len(datasets) == len(charts) == 3  # DataLens keeps one dataset per chart
    for dataset, chart in zip(datasets, charts, strict=True):
        assert chart.payload["data"]["datasetsIds"] == [dataset.native_id]

    dashboard = artifacts_of(contract, BIArtifactKind.DASHBOARD)[0]
    assert "schemeVersion" not in dashboard.payload  # injected server-side, never compiled
    assert len(dashboard.payload["tabs"][0]["items"]) >= len(charts)


def test_datalens_contract_omits_the_dwh_connection(demo_model: SemanticModel) -> None:
    """§10.2: the connection payload carries DWH credentials and is environment, not compile."""
    contract = DataLensContractCompiler().compile_contract(make_spec(TargetBI.DATALENS), demo_model)
    assert {item.kind for item in contract.artifacts} == {
        BIArtifactKind.DATASET,
        BIArtifactKind.CHART,
        BIArtifactKind.DASHBOARD,
    }
    blob = str([item.payload for item in contract.artifacts])
    assert "password" not in blob
