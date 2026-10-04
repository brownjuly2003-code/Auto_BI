"""Run identity: the idempotency key covers every static gate input.

Durable spec: docs/specs/run-identity.md. Two runs that differ in an input the gate is handed
are two runs; what was observed at the moment of a run — the recorded results, the engine's
readings, `created_at` — is not part of what a run is.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import json
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

import auto_bi.verification.release_gate as gate
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import ValidationReport, load_contracts
from auto_bi.evidence.manifest import EvidenceConflictError, read_bundle, verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.gateway import NativeModelSource, SemanticGap, SemanticSnapshot
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.reconcile import ResultSet
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateInputs,
    GateReport,
    RecordedResult,
    RecordedResults,
    idempotency_key,
    run_gate,
    run_id_for,
)
from auto_bi.verification.repository import VerifiedAsset, VerifiedAssetRepository
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    ENVIRONMENT_REF,
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    attestation,
    dashboard_spec,
    environment,
    metric_contract,
    product_contract,
    recorded_results,
    revenue_metric,
    semantic_model,
    status_of,
    summary_of,
    verified_asset,
)

ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]
OTHER_ROWS = [["Москва", "0.500"], ["Казань", "0.377"]]
METRIC_ID = "metric://sales/gross_margin_pct"
DASHBOARD_REF = "dashboard://weekly_margin@7"
OTHER_DIGEST = "sha256:" + "0" * 64


def _case(root: Path) -> Case:
    return Case(root).with_asset().with_results(ROWS)


def _run(case: Case) -> GateReport:
    return case.run(product_id=PRODUCT_ID)


def _store(root: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(root / "evidence")


def _artifacts(root: Path, report: GateReport) -> dict[str, str]:
    bundle = read_bundle(_store(root), report.run_id)
    return {item.artifact_id: item.digest for item in bundle.artifacts}


def _graded(report: GateReport) -> list[tuple[str, CheckStatus, str]]:
    return [(check.check_id, check.status, check.summary_code) for check in report.checks]


# --- one changed static input, laid out on disk --------------------------------


def _validated_draft(case: Case) -> None:
    contract = metric_contract()
    contract["metadata"]["status"] = "draft"
    _write_yaml(case.contracts / "gross_margin_pct.yaml", contract)


def _validated_description(case: Case) -> None:
    contract = metric_contract()
    contract["metadata"]["description"] = "another wording"
    _write_yaml(case.contracts / "gross_margin_pct.yaml", contract)


def _unpinned_validated_metric(case: Case) -> None:
    _write_yaml(case.contracts / "revenue.yaml", revenue_metric())


def _second_product(case: Case) -> None:
    other = product_contract()
    other["metadata"]["id"] = "decision://sales/other_margin"
    _write_yaml(case.contracts / "other_margin.yaml", other)


def _asset_expectation(case: Case) -> None:
    asset = verified_asset(case.base_digest)
    asset["expectation"]["result"]["rows"] = OTHER_ROWS
    _write_yaml(case.root / "assets" / "weekly_margin_by_region.yaml", asset)


def _asset_rationale(case: Case) -> None:
    asset = verified_asset(case.base_digest)
    asset["review"]["rationale"] = "another rationale"
    _write_yaml(case.root / "assets" / "weekly_margin_by_region.yaml", asset)


def _dashboard_measure(case: Case) -> None:
    spec = dashboard_spec()
    spec["charts"][0]["query"]["measures"] = [{"column": "gross_margin", "agg": "sum"}]
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)


def _dashboard_invalid(case: Case) -> None:
    spec = dashboard_spec()
    spec["charts"][0]["query"]["measures"] = [{"column": "no_such_column", "agg": "sum"}]
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)


def _candidate_broken_contract(case: Case) -> None:
    (case.candidate / "metrics" / "broken.yaml").write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/x}\n", encoding="utf-8"
    )


def _comment(path: Path) -> None:
    path.write_bytes(path.read_bytes() + b"\n# a comment that changes no meaning\n")


def _candidate_model_comment(case: Case) -> None:
    _comment(case.candidate / "model.yaml")


def _base_model_comment(case: Case) -> None:
    _comment(case.base / "model.yaml")


def _candidate_model_statistics(case: Case) -> None:
    model = semantic_model()
    physical = model.tables[0].physical
    assert physical is not None
    physical.rows = 123_456
    physical.cardinality = {"region": 12}
    model.tables[0].columns[2].top_values = ["Москва", "Казань"]
    model.dump(case.candidate / "model.yaml")


Change = Callable[[Case], None]


def _pair(root: Path, change: Change) -> tuple[GateReport, GateReport]:
    """Two runs in two directories; the second after *change* touched one input."""
    first = _run(_case(root / "a"))
    changed = _case(root / "b")
    change(changed)
    return first, _run(changed)


@pytest.mark.parametrize(
    "change",
    [
        _validated_draft,
        _validated_description,
        _unpinned_validated_metric,
        _second_product,
        _asset_expectation,
        _asset_rationale,
        _dashboard_measure,
        _dashboard_invalid,
        _candidate_broken_contract,
        _candidate_model_comment,
        _base_model_comment,
        _candidate_model_statistics,
    ],
    ids=lambda change: change.__name__.lstrip("_"),
)
def test_a_changed_static_input_is_another_run(tmp_path: Path, change: Change) -> None:
    first, second = _pair(tmp_path, change)

    assert first.idempotency_key != second.idempotency_key
    assert first.run_id != second.run_id


def test_a_validated_contract_that_changed_is_another_run(tmp_path: Path) -> None:
    first, second = _pair(tmp_path, _validated_draft)

    assert first.run_id != second.run_id
    assert first.verdict is Verdict.PASS_WITH_ADVISORY
    assert status_of(first, "contracts.validate") is CheckStatus.PASS
    assert summary_of(first, "contracts.validate") == "contracts.clean"
    assert second.verdict is Verdict.BLOCK
    assert status_of(second, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(second, "contracts.validate") == "contracts.issues"


@pytest.mark.parametrize("change", [_unpinned_validated_metric, _second_product])
def test_a_validated_contract_the_product_does_not_pin_is_part_of_the_identity(
    tmp_path: Path, change: Change
) -> None:
    first, second = _pair(tmp_path, change)

    assert first.run_id != second.run_id
    assert _graded(first) == _graded(second)
    report = _check_payload(tmp_path / "b", second, "contracts.validate")
    assert len(report["products"]) + len(report["metrics"]) == 3


def test_gate_inputs_with_and_without_the_validated_contracts_are_two_runs(
    tmp_path: Path,
) -> None:
    draft = metric_contract()
    draft["metadata"]["status"] = "draft"
    case = Case(tmp_path / "case").with_candidate(contract=draft)
    loaded = load_contracts([case.contracts])
    reports = []
    for name, validated in (("without", {}), ("compared", loaded.metrics)):
        inputs = GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=_snapshot(case.base),
            candidate=_snapshot(case.candidate),
            candidate_model=semantic_model(),
            validated_metrics=validated,
        )
        reports.append(run_gate(inputs, store=_store(tmp_path / name), created_at=CREATED_AT))
    without, compared = reports

    assert without.run_id != compared.run_id
    assert status_of(without, "contracts.validate") is CheckStatus.PASS
    assert summary_of(without, "contracts.validate") == "contracts.clean"
    assert status_of(compared, "contracts.validate") is CheckStatus.REVIEW
    payload = _check_payload(tmp_path / "compared", compared, "contracts.validate")
    assert [issue["code"] for issue in payload["issues"]] == ["metric.candidate_draft"]


def test_a_verified_asset_that_changed_is_another_run(tmp_path: Path) -> None:
    first, second = _pair(tmp_path, _asset_expectation)

    assert first.run_id != second.run_id
    assert status_of(first, "results.reconcile") is CheckStatus.PASS
    assert summary_of(first, "results.reconcile") == "result.equivalent"
    assert status_of(second, "results.reconcile") is CheckStatus.BLOCK
    assert summary_of(second, "results.reconcile") == "result.not_equivalent"


def test_an_input_that_changes_no_finding_still_moves_the_run_id(tmp_path: Path) -> None:
    first, second = _pair(tmp_path, _asset_rationale)

    assert first.run_id != second.run_id
    assert _graded(first) == _graded(second)
    before, after = _artifacts(tmp_path / "a", first), _artifacts(tmp_path / "b", second)
    # The owner summary names the run id; nothing the checks sealed moved.
    assert [key for key in before if before[key] != after[key]] == ["report-summary-md"]
    assert before.keys() == after.keys()


def test_a_dashboard_spec_that_changed_is_another_run(tmp_path: Path) -> None:
    first, second = _pair(tmp_path / "measure", _dashboard_measure)
    assert first.run_id != second.run_id
    assert summary_of(second, "bi.contract") == "bi.compiled"

    first, second = _pair(tmp_path / "invalid", _dashboard_invalid)
    assert first.run_id != second.run_id
    assert status_of(first, "bi.contract") is CheckStatus.PASS
    assert summary_of(first, "bi.contract") == "bi.compiled"
    assert status_of(second, "bi.contract") is CheckStatus.BLOCK
    assert summary_of(second, "bi.contract") == "bi.spec_invalid"


def test_snapshot_gaps_are_part_of_the_identity(tmp_path: Path) -> None:
    first, second = _pair(tmp_path, _candidate_broken_contract)

    assert first.candidate_digest == second.candidate_digest
    assert first.run_id != second.run_id
    assert status_of(first, "semantic.gaps") is CheckStatus.PASS
    assert status_of(second, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(second, "semantic.gaps") == "semantic.gaps_present"


@pytest.mark.parametrize(
    "change", [_candidate_model_comment, _base_model_comment, _candidate_model_statistics]
)
def test_the_bytes_a_snapshot_was_read_from_are_part_of_the_identity(
    tmp_path: Path, change: Change
) -> None:
    first, second = _pair(tmp_path, change)

    assert first.base_digest == second.base_digest
    assert first.candidate_digest == second.candidate_digest
    assert first.run_id != second.run_id
    assert _graded(first) == _graded(second)


# --- the fields of the gate inputs ---------------------------------------------


def _snapshot(root: Path) -> SemanticSnapshot:
    return NativeModelSource(root / "model.yaml", root / "metrics").snapshot()


def _asset(semantic_digest: str, **changes: Any) -> VerifiedAsset:
    raw = verified_asset(semantic_digest)
    raw["metadata"].update(changes.pop("metadata", {}))
    raw["review"].update(changes.pop("review", {}))
    return VerifiedAsset.model_validate({**raw, **changes})


def _inputs(root: Path) -> GateInputs:
    case = _case(root)
    loaded = load_contracts([case.contracts])
    recorded = recorded_results(ROWS, attestation(case.candidate_digest))[ASSET_ID]
    return GateInputs(
        product=next(iter(loaded.products.values())),
        validation=loaded.report,
        validated_metrics=loaded.metrics,
        base=_snapshot(case.base),
        candidate=_snapshot(case.candidate),
        candidate_model=SemanticModel.load(case.candidate / "model.yaml"),
        dashboards={DASHBOARD_REF: DashboardSpec.model_validate(dashboard_spec())},
        assets=VerifiedAssetRepository([_asset(case.base_digest)]),
        results=RecordedResults({ASSET_ID: RecordedResult.model_validate(recorded)}),
        environment_ref=ENVIRONMENT_REF,
        environment=environment(),
    )


def _other_product(inputs: GateInputs) -> Any:
    raw = product_contract()
    raw["metadata"]["owner"] = "another-team"
    return DecisionProductSpec.model_validate(raw)


def _other_validation(inputs: GateInputs) -> Any:
    products = [*inputs.validation.products, "decision://sales/other_margin@1"]
    return inputs.validation.model_copy(update={"products": products})


def _other_validated_metrics(inputs: GateInputs) -> Any:
    raw = metric_contract()
    raw["metadata"]["description"] = "another wording"
    return {METRIC_ID: MetricContract.model_validate(raw)}


def _other_base(inputs: GateInputs) -> Any:
    return inputs.base.model_copy(update={"source_digest": OTHER_DIGEST})


def _other_candidate(inputs: GateInputs) -> Any:
    return inputs.candidate.model_copy(update={"source_digest": OTHER_DIGEST})


def _other_candidate_model(inputs: GateInputs) -> Any:
    model = inputs.candidate_model.model_copy(deep=True)
    physical = model.tables[0].physical
    assert physical is not None
    physical.rows = 5
    return model


def _other_dashboards(inputs: GateInputs) -> Any:
    spec = {**dashboard_spec(), "title": "Другая маржа"}
    return {DASHBOARD_REF: DashboardSpec.model_validate(spec)}


def _other_assets(inputs: GateInputs) -> Any:
    digest = inputs.assets.assets[0].semantic_digest
    return VerifiedAssetRepository([_asset(digest, review={"rationale": "another rationale"})])


def _other_results(inputs: GateInputs) -> Any:
    result = ResultSet.model_validate(recorded_results(OTHER_ROWS, None)[ASSET_ID])
    return RecordedResults({ASSET_ID: RecordedResult(result=result)})


def _other_environment(inputs: GateInputs) -> Any:
    return environment(max_event_time="2026-08-25T06:30:00Z")


STATIC: dict[str, Callable[[GateInputs], Any]] = {
    "product": _other_product,
    "validation": _other_validation,
    "validated_metrics": _other_validated_metrics,
    "base": _other_base,
    "candidate": _other_candidate,
    "candidate_model": _other_candidate_model,
    "dashboards": _other_dashboards,
    "assets": _other_assets,
    "environment_ref": lambda inputs: "env://staging@1",
}
MOMENT: dict[str, Callable[[GateInputs], Any]] = {
    "results": _other_results,
    "environment": _other_environment,
}


def test_the_gate_inputs_have_no_field_outside_the_two_kinds() -> None:
    assert {field.name for field in dataclasses.fields(GateInputs)} == {*STATIC, *MOMENT}


@pytest.mark.parametrize("name", sorted(STATIC))
def test_a_static_field_of_the_gate_inputs_moves_the_key(tmp_path: Path, name: str) -> None:
    inputs = _inputs(tmp_path)
    other = dataclasses.replace(inputs, **{name: STATIC[name](inputs)})

    assert idempotency_key(other) != idempotency_key(inputs)
    assert run_id_for(idempotency_key(other)) != run_id_for(idempotency_key(inputs))


@pytest.mark.parametrize("name", sorted(MOMENT))
def test_an_observation_of_the_moment_does_not_move_the_key(tmp_path: Path, name: str) -> None:
    inputs = _inputs(tmp_path)
    other = dataclasses.replace(inputs, **{name: MOMENT[name](inputs)})

    assert idempotency_key(other) == idempotency_key(inputs)


@pytest.mark.parametrize("side", ["base", "candidate"])
@pytest.mark.parametrize(
    "update",
    [
        {
            "gaps": [
                SemanticGap(
                    code="gap.note", severity=Severity.ADVISORY, path="model.yaml", reason="a note"
                )
            ]
        },
        {"source_digest": OTHER_DIGEST},
        {"source_revision": "refs/heads/release"},
        {"capabilities": ["a_capability_no_source_declares"]},
    ],
    ids=["gaps", "source-digest", "source-revision", "capabilities"],
)
def test_a_snapshot_is_part_of_the_key_whole(
    tmp_path: Path, side: str, update: dict[str, Any]
) -> None:
    inputs = _inputs(tmp_path)
    snapshot = getattr(inputs, side)
    assert getattr(snapshot, next(iter(update))) != next(iter(update.values()))
    other = dataclasses.replace(inputs, **{side: snapshot.model_copy(update=update)})

    assert other.base.canonical_digest == inputs.base.canonical_digest
    assert other.candidate.canonical_digest == inputs.candidate.canonical_digest
    assert idempotency_key(other) != idempotency_key(inputs)


def test_the_order_inputs_are_handed_in_does_not_move_the_key(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    revenue = MetricContract.model_validate(revenue_metric())
    margin = inputs.validated_metrics[METRIC_ID]
    spec = inputs.dashboards[DASHBOARD_REF]
    other_spec = DashboardSpec.model_validate({**dashboard_spec(), "title": "Выручка"})
    asset = inputs.assets.assets[0]
    other_asset = _asset(
        asset.semantic_digest, metadata={"id": "asset://sales/weekly_revenue_by_region"}
    )
    metrics = [(METRIC_ID, margin), ("metric://sales/revenue", revenue)]
    dashboards = [(DASHBOARD_REF, spec), ("dashboard://weekly_revenue@1", other_spec)]
    assets = [asset, other_asset]

    forward = dataclasses.replace(
        inputs,
        validated_metrics=dict(metrics),
        dashboards=dict(dashboards),
        assets=VerifiedAssetRepository(assets),
    )
    backward = dataclasses.replace(
        inputs,
        validated_metrics=dict(reversed(metrics)),
        dashboards=dict(reversed(dashboards)),
        assets=VerifiedAssetRepository(list(reversed(assets))),
    )

    assert list(forward.validated_metrics) != list(backward.validated_metrics)
    assert idempotency_key(forward) == idempotency_key(backward)
    assert idempotency_key(forward) != idempotency_key(inputs)


#: Types whose dump has no order of its own, or no dump the key has a text for.
UNORDERED = (set, frozenset, collections.abc.Set, bytes, bytearray, object, Any)


def _named(annotation: Any) -> Any:
    """The type a `type X = …` statement names: such an alias has no origin of its own."""
    while isinstance(annotation, typing.TypeAliasType):
        annotation = annotation.__value__
    return annotation


def _held(annotation: Any, path: str, seen: set[type[BaseModel]]) -> Iterator[tuple[str, Any]]:
    """Every type a dumped input can hold, with the field it is declared under."""
    annotation = _named(annotation)
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        yield from _held(typing.get_args(annotation)[0], path, seen)
        return
    yield path, annotation
    if origin is typing.Literal:
        return
    if origin is not None:
        for argument in typing.get_args(annotation):
            if argument is not Ellipsis:
                yield from _held(argument, path, seen)
    elif _is_model(annotation) and annotation not in seen:
        seen.add(annotation)
        for name, info in annotation.model_fields.items():
            yield from _held(info.annotation, f"{path}.{name}", seen)


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_text(annotation: Any) -> bool:
    annotation = _named(annotation)
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = _named(typing.get_args(annotation)[0])
    if typing.get_origin(annotation) is typing.Literal:
        return all(isinstance(value, str) for value in typing.get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, str)


def _without_order(model: type[BaseModel]) -> list[str]:
    """Fields whose dump the key could not write the same way in every process."""
    found = []
    for path, kind in _held(model, model.__name__, set()):
        origin = typing.get_origin(kind) or kind
        keyed = origin in (dict, collections.abc.Mapping)
        if origin in UNORDERED or (keyed and not _is_text(typing.get_args(kind)[0])):
            found.append(path)
    return found


@pytest.mark.parametrize(
    "model",
    [
        ValidationReport,
        MetricContract,
        SemanticSnapshot,
        SemanticModel,
        DashboardSpec,
        VerifiedAsset,
    ],
    ids=lambda model: model.__name__,
)
def test_no_static_input_is_dumped_in_an_order_the_process_chooses(
    model: type[BaseModel],
) -> None:
    assert len(list(_held(model, model.__name__, set()))) > 10
    assert _without_order(model) == []


def test_a_field_declared_through_a_type_alias_is_read_through() -> None:
    held = {path for path, _ in _held(VerifiedAsset, "VerifiedAsset", set())}

    assert "VerifiedAsset.tolerance.aggregate_checks.share_column" in held
    assert "VerifiedAsset.tolerance.aggregate_checks.component_columns" in held


class _Unordered(BaseModel):
    tags: set[str] = set()
    raw: bytes = b""


type _Aliased = typing.Annotated[frozenset[str], "named by a type statement"]
type _Name = str


class _Holder(BaseModel):
    inner: list[_Unordered] = []
    by_number: dict[int, str] = {}
    anything: Any = None
    by_name: dict[str, list[int]] = {}
    aliased: list[_Aliased] = []
    by_alias: dict[_Name, int] = {}


def test_a_field_without_an_order_would_be_named() -> None:
    assert _without_order(_Holder) == [
        "_Holder.inner.tags",
        "_Holder.inner.raw",
        "_Holder.by_number",
        "_Holder.anything",
        "_Holder.aliased",
    ]


# --- observations of a moment ---------------------------------------------------


def test_other_recorded_results_do_not_move_the_run_id(tmp_path: Path) -> None:
    first = _run(_case(tmp_path / "a"))
    second = _run(Case(tmp_path / "b").with_asset().with_results(OTHER_ROWS))

    assert first.run_id == second.run_id
    assert first.evidence_digest != second.evidence_digest
    assert summary_of(first, "results.reconcile") == "result.equivalent"
    assert summary_of(second, "results.reconcile") == "result.not_equivalent"


@pytest.mark.parametrize("observation", ["readings", "created_at"])
def test_another_observation_shares_the_run_id_and_is_refused_in_one_store(
    tmp_path: Path, observation: str
) -> None:
    inputs = _inputs(tmp_path / "case")
    store = _store(tmp_path)
    first = run_gate(inputs, store=store, created_at=CREATED_AT)
    other, created_at = (
        (dataclasses.replace(inputs, environment=_other_environment(inputs)), CREATED_AT)
        if observation == "readings"
        else (inputs, "2026-08-25T09:00:01Z")
    )

    assert run_id_for(idempotency_key(other)) == first.run_id
    with pytest.raises(EvidenceConflictError, match="same idempotency key, different evidence"):
        run_gate(other, store=store, created_at=created_at)
    assert verify_bundle(store, first.run_id) == []
    assert read_bundle(store, first.run_id).created_at == CREATED_AT


def test_equal_inputs_and_equal_observations_write_one_bundle(tmp_path: Path) -> None:
    first = _run(_case(tmp_path / "a"))
    second = _run(_case(tmp_path / "b"))

    assert first.idempotency_key == second.idempotency_key
    assert first.run_id == second.run_id
    assert first.evidence_digest == second.evidence_digest


# --- a run repeated into the same evidence directory ----------------------------


def test_the_validated_contract_is_corrected_and_the_gate_is_run_again(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _validated_draft(case)
    first = _run(case)
    assert first.verdict is Verdict.BLOCK

    _write_yaml(case.contracts / "gross_margin_pct.yaml", metric_contract())
    second = _run(case)

    assert second.verdict is Verdict.PASS_WITH_ADVISORY
    assert second.run_id != first.run_id
    store = _store(tmp_path)
    assert verify_bundle(store, first.run_id) == []
    assert verify_bundle(store, second.run_id) == []
    assert read_bundle(store, first.run_id).run_id == first.run_id


@pytest.mark.parametrize(
    "change",
    [
        _asset_expectation,
        _asset_rationale,
        _dashboard_measure,
        _candidate_model_comment,
        _base_model_comment,
    ],
    ids=lambda change: change.__name__.lstrip("_"),
)
def test_a_changed_static_input_is_written_beside_the_first_bundle(
    tmp_path: Path, change: Change
) -> None:
    case = _case(tmp_path)
    first = _run(case)
    sealed = _artifacts(tmp_path, first)

    change(case)
    second = _run(case)

    assert second.run_id != first.run_id
    store = _store(tmp_path)
    assert verify_bundle(store, first.run_id) == []
    assert verify_bundle(store, second.run_id) == []
    assert _artifacts(tmp_path, first) == sealed
    assert sorted(path.name for path in (tmp_path / "evidence").iterdir()) == sorted(
        [first.run_id, second.run_id]
    )


# --- values the canonical contract form refuses ---------------------------------


def _spec_text(value: str, column: str = "region") -> str:
    spec = dashboard_spec()
    spec["charts"][0]["query"]["filters"] = [{"column": column, "op": "=", "value": "VALUE"}]
    text: str = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    assert text.count("value: VALUE") == 1
    return text.replace("value: VALUE", f"value: {value}")


def _run_with_dashboard(root: Path, text: str) -> GateReport:
    case = Case(root)
    (case.dashboards / "weekly_margin@7.yaml").write_text(text, encoding="utf-8")
    return case.run()


def test_an_invalid_dashboard_spec_with_a_non_finite_filter_value_is_still_graded(
    tmp_path: Path,
) -> None:
    reports = [
        _run_with_dashboard(tmp_path / name, _spec_text(value, column="no_such_column"))
        for name, value in (("nan", ".nan"), ("inf", ".inf"))
    ]

    for report in reports:
        assert status_of(report, "bi.contract") is CheckStatus.BLOCK
        assert summary_of(report, "bi.contract") == "bi.spec_invalid"
    assert reports[0].run_id != reports[1].run_id


def test_integers_past_the_safe_range_are_told_apart(tmp_path: Path) -> None:
    reports = {
        value: _run_with_dashboard(tmp_path / value, _spec_text(value))
        for value in ("9007199254740992", "9007199254740993")
    }
    payloads = [
        _check_payload(tmp_path / value, reports[value], "bi.contract") for value in reports
    ]

    for report in reports.values():
        assert summary_of(report, "bi.contract") == "bi.compiled"
    assert payloads[0] == payloads[1]
    assert reports["9007199254740992"].run_id != reports["9007199254740993"].run_id


def test_an_integer_and_the_float_it_equals_are_told_apart(tmp_path: Path) -> None:
    integer = _run_with_dashboard(tmp_path / "integer", _spec_text("1"))
    floating = _run_with_dashboard(tmp_path / "float", _spec_text("1.0"))

    assert summary_of(integer, "bi.contract") == "bi.compiled"
    assert summary_of(floating, "bi.contract") == "bi.compiled"
    assert integer.run_id != floating.run_id


def test_statistics_of_any_size_are_part_of_the_identity(tmp_path: Path) -> None:
    plain = Case(tmp_path / "plain").run()
    reports = []
    for name, rows in (("one", 10**400), ("other", 10**400 + 1)):
        model = semantic_model()
        physical = model.tables[0].physical
        assert physical is not None
        physical.rows = rows
        reports.append(Case(tmp_path / name).with_candidate(model=model).run())

    for report in reports:
        assert report.verdict is plain.verdict
        assert _graded(report) == _graded(plain)
    assert reports[0].candidate_digest == reports[1].candidate_digest == plain.candidate_digest
    assert len({plain.run_id, reports[0].run_id, reports[1].run_id}) == 3


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _model_synonym(root: Path, escape: str, code_points: int = 1) -> GateReport:
    case = Case(root).with_candidate()
    _replace_once(
        case.candidate / "model.yaml",
        "role: dimension\n",
        f'role: dimension\n    synonyms: ["{escape}"]\n',
    )
    synonyms = SemanticModel.load(case.candidate / "model.yaml").tables[0].columns[1].synonyms
    assert [len(item) for item in synonyms] == [code_points]
    return case.run()


def _contract_description(root: Path, escape: str) -> GateReport:
    case = Case(root)
    _replace_once(
        case.contracts / "gross_margin_pct.yaml",
        "owner: commercial-analytics\n",
        f'owner: commercial-analytics\n  description: "{escape}"\n',
    )
    loaded = load_contracts([case.contracts])
    assert len(loaded.metrics[METRIC_ID].metadata.description) == 1
    return case.run()


@pytest.mark.parametrize("run", [_model_synonym, _contract_description])
def test_a_lone_surrogate_outside_the_sealed_evidence_does_not_stop_the_run(
    tmp_path: Path, run: Callable[[Path, str], GateReport]
) -> None:
    first = run(tmp_path / "d800", "\\ud800")
    second = run(tmp_path / "d801", "\\ud801")

    assert first.verdict is Verdict.PASS_WITH_ADVISORY
    assert second.verdict is Verdict.PASS_WITH_ADVISORY
    assert first.run_id != second.run_id


def test_a_character_and_the_surrogates_that_would_spell_it_are_two_runs(
    tmp_path: Path,
) -> None:
    spelled = _model_synonym(tmp_path / "surrogates", "\\ud83d\\ude00", code_points=2)
    written = _model_synonym(tmp_path / "character", chr(0x1F600))

    assert spelled.verdict is Verdict.PASS_WITH_ADVISORY
    assert written.verdict is Verdict.PASS_WITH_ADVISORY
    assert spelled.run_id != written.run_id


def test_the_exact_text_of_an_input() -> None:
    value = {
        "b": [1, -1, 2**53 + 1, 1.5, -0.0, float("inf"), float("nan"), None, True, False],
        "a": 'ж\ud800"',
        "": {},
    }

    assert gate.identity_text(value) == (
        "{'':{},'a':'\\u0436\\ud800\"','b':[0x1,-0x1,0x20000000000001,"
        "0x1.8000000000000p+0,-0x0.0p+0,inf,nan,null,true,false]}"
    )
    assert gate.identity_text(10**5000) == hex(10**5000)
    assert gate.identity_text(1) != gate.identity_text(1.0)
    assert gate.identity_text(2**53) != gate.identity_text(2**53 + 1)
    assert gate.identity_text({"a": 1, "b": 2}) == gate.identity_text({"b": 2, "a": 1})
    assert gate.identity_text([1, 2]) != gate.identity_text([2, 1])


@pytest.mark.parametrize(
    "points",
    [
        [],
        [0x61],
        [0x27],
        [0x22],
        [0x27, 0x22],
        [0x5C],
        [0x0A],
        [0x00],
        [0x7F],
        [0xE9],
        [0x436],
        [0xD800],
        [0x1F600],
        [0xD83D, 0xDE00],
        [0x61, 0x27, 0x2C, 0x27, 0x62],
    ],
)
def test_the_text_of_a_string_reads_back_as_that_string(points: list[int]) -> None:
    value = "".join(map(chr, points))
    text = gate.identity_text(value)

    assert text.isascii()
    assert ast.literal_eval(text) == value
    assert gate.identity_text({value: value}) == "{" + text + ":" + text + "}"


def test_a_character_and_the_surrogates_that_would_spell_it_are_two_texts() -> None:
    character, surrogates = chr(0x1F600), chr(0xD83D) + chr(0xDE00)

    # Why a JSON text cannot serve: it spells both the same way.
    assert json.dumps(character) == json.dumps(surrogates)
    assert gate.identity_text(character) != gate.identity_text(surrogates)
    assert gate.identity_text(["a", "b"]) != gate.identity_text(["a','b"])


@pytest.mark.parametrize("value", [{1: "a"}, (1, 2), {"a": {1}}, object()])
def test_the_exact_text_refuses_what_is_not_a_dumped_value(value: object) -> None:
    with pytest.raises(TypeError):
        gate.identity_text(value)


def test_versions() -> None:
    assert VERIFIER_VERSION == "91"
    assert CHECK_VERSION == "66"
