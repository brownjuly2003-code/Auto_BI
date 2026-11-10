"""Delivered specs validate before the pure BI compile seam (T-35)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auto_bi.decision.spec import DashboardDelivery
from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.evidence.ids import slugify
from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.store import ArtifactStoreError
from auto_bi.ir.spec import DashboardSpec
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.gateway import SemanticSnapshot, _build_graph
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.model import SemanticModel
from auto_bi.verification import release_gate
from auto_bi.verification.release_gate import GateInputs, GateReport, run_gate
from tests.test_semantic_gateway import contract, model

REF = "dashboard://weekly_margin@7"
SECOND_REF = "dashboard://weekly_margin@8"
CHECK_PATH = "checks/check-bi.contract.json"
DIGESTS = {
    "superset": {
        "dataset-1": "sha256:bebb199790a476a0bfb07778283a49b7b334e6321b40900da0831246d4572a27",
        "chart-1": "sha256:32af50e19166190b055853315ed26de52b6c119b01706451f1fce429164d1aac",
        "dashboard": "sha256:6962c600b4436f65ea1c0b23c73f742078e9be7b88c28b87f78394e403f863f9",
    },
    "datalens": {
        "dataset-1": "sha256:4ef09e9b7b42173fdd21cc6676297dcf932388b5bfc498772e91ed524bcea0c5",
        "chart-1": "sha256:eb269065d47a1fd21a7808271db91cd2182db408684b76b088d02483f2ebcaf9",
        "dashboard": "sha256:ae6e20035e812d63a2735226298aad01a8d3a7b54c260143f5875d5b557762d6",
    },
}


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], bytes] = {}

    def put(self, run_id: str, path: str, payload: bytes) -> None:
        self.data[run_id, path] = payload

    def exists(self, run_id: str, path: str) -> bool:
        return (run_id, path) in self.data

    def get(self, run_id: str, path: str) -> bytes:
        try:
            return self.data[run_id, path]
        except KeyError:
            raise ArtifactStoreError(path) from None


def spec(
    target: str = "superset",
    *,
    column: str = "revenue",
    table: str = "dm.sales_daily",
    dimension: str | None = None,
    filter_column: str | None = None,
    filter_type: str = "select",
    raw_sql: str | None = None,
) -> DashboardSpec:
    query: dict[str, Any] = {"table": table}
    if raw_sql is None:
        query["measures"] = [{"column": column, "agg": "sum"}]
    else:
        query["raw_sql"] = raw_sql
    if dimension:
        query["dimensions"] = [dimension]
    return DashboardSpec.model_validate(
        {
            "title": "Probe",
            "target_bi": target,
            "charts": [
                {
                    "id": "t" if raw_sql else "by_region" if dimension else "kpi",
                    "title": "KPI",
                    "viz": "table" if raw_sql else "bar" if dimension else "big_number",
                    "query": query,
                }
            ],
            "filters": ([{"column": filter_column, "type": filter_type}] if filter_column else []),
        }
    )


def run(
    dashboards: dict[str, DashboardSpec],
    *,
    candidate_model: SemanticModel | None = None,
    two: bool = False,
) -> tuple[GateReport, MemoryStore]:
    base_model = model()
    gaps = []
    graph = _build_graph(base_model, [MetricContract.model_validate(contract())], gaps)
    digest = sha256_digest(graph.model_dump(mode="json"))
    snapshot = SemanticSnapshot(
        source_kind="native_model_yaml",
        source_version="1",
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
        gaps=gaps,
    )
    loaded = load_contracts([Path(__file__).parent / "fixtures" / "decision"])
    product = next(iter(loaded.products.values()))
    if two:
        product = product.model_copy(
            update={
                "delivery": product.delivery.model_copy(
                    update={
                        "dashboards": [
                            *product.delivery.dashboards,
                            DashboardDelivery(ref=SECOND_REF),
                        ]
                    }
                )
            }
        )
    store = MemoryStore()
    report = run_gate(
        GateInputs(
            product=product,
            validation=loaded.report,
            base=snapshot,
            candidate=snapshot,
            candidate_model=candidate_model if candidate_model is not None else base_model,
            dashboards=dashboards,
        ),
        store=store,
        created_at="2026-10-02T00:00:00Z",
    )
    return report, store


def assert_check(
    report: GateReport,
    store: MemoryStore,
    *,
    invalid: list[dict[str, Any]],
    compiled: list[str],
    missing: list[str] | None = None,
    status: str = "block",
) -> dict[str, Any]:
    missing = missing or []
    check = next(c for c in report.checks if c.check_id == "bi.contract")
    assert check.status.value == status
    assert check.summary_code == (
        "bi.spec_invalid" if invalid else "bi.spec_missing" if missing else "bi.compiled"
    )
    detail = f"{len(compiled)} dashboard(s) compiled without touching the BI"
    if invalid:
        detail += (
            "; not compiled, the spec does not validate against the candidate semantic model: "
            + ", ".join(item["dashboard"] for item in invalid)
        )
    if missing:
        detail += "; no spec supplied for " + ", ".join(missing)
    assert check.detail == detail
    payload = json.loads(store.get(report.run_id, CHECK_PATH))
    assert set(payload) == {"compiled", "invalid", "missing", "refused"}
    assert payload["invalid"] == invalid
    assert payload["missing"] == missing
    assert payload["refused"] == []
    assert [item["dashboard"] for item in payload["compiled"]] == compiled
    artifacts = read_bundle(store, report.run_id).artifacts
    for item in invalid:
        prefix = "bi-" + slugify(item["dashboard"]) + "-"
        assert not any(
            artifact.kind.value == "bi_contract" and artifact.artifact_id.startswith(prefix)
            for artifact in artifacts
        )
    if invalid:
        assert report.verdict.value == "block"
    return payload


def assert_digests(report: GateReport, store: MemoryStore, target: str) -> None:
    artifacts = [
        a for a in read_bundle(store, report.run_id).artifacts if a.kind.value == "bi_contract"
    ]
    identity = "dashboard://weekly_margin@7"
    assert {a.artifact_id: a.digest for a in artifacts} == {
        f"bi-dashboard-weekly-margin-7-{suffix}-"
        + sha256_digest([identity, suffix]).removeprefix("sha256:"): digest
        for suffix, digest in DIGESTS[target].items()
    }
    for artifact in artifacts:
        assert digest_bytes(store.get(report.run_id, artifact.path)) == artifact.digest


@pytest.mark.parametrize(
    ("dashboard", "expected"),
    [
        (
            spec(column="ghost_revenue"),
            "chart 'kpi': unknown measure column 'ghost_revenue' in dm.sales_daily",
        ),
        (
            spec("datalens", column="ghost_revenue"),
            "chart 'kpi': unknown measure column 'ghost_revenue' in dm.sales_daily",
        ),
        (
            spec(table="dm.ghost"),
            "chart 'kpi': unknown table 'dm.ghost' (known tables: dm.sales_daily, dm.stores)",
        ),
        (
            spec(dimension="ghost_region"),
            "chart 'by_region': unknown dimension column 'ghost_region' in dm.sales_daily",
        ),
        (
            spec(dimension="region", filter_column="dm.sales_daily.ghost"),
            "dashboard filter references unknown column: 'dm.sales_daily.ghost'",
        ),
        (
            spec(raw_sql="this is not SQL"),
            "chart 't': raw_sql is not a single plain SELECT: only SELECT is allowed, got Not",
        ),
        (
            spec("datalens", raw_sql="SELECT region FROM dm.sales_daily"),
            "chart 't': raw_sql is supported only with target_bi=superset, got 'datalens'",
        ),
    ],
    ids=[
        "unknown-measure-superset",
        "unknown-measure-datalens",
        "unknown-table",
        "unknown-dimension",
        "unknown-filter",
        "refused-operator-sql",
        "datalens-operator-sql",
    ],
)
def test_invalid_spec_blocks_before_compile(
    dashboard: DashboardSpec, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = validate_spec(dashboard, model())
    assert errors == [expected]
    calls = []
    original = release_gate._compile

    def spy(dashboard: DashboardSpec, candidate: SemanticModel) -> Any:
        calls.append(dashboard)
        return original(dashboard, candidate)

    monkeypatch.setattr(release_gate, "_compile", spy)
    report, store = run({REF: dashboard})
    assert_check(report, store, invalid=[{"dashboard": REF, "errors": errors}], compiled=[])
    assert calls == []


def test_candidate_dropped_column_blocks() -> None:
    dashboard = spec()
    candidate = model().model_copy(deep=True)
    candidate.tables[0].columns = [c for c in candidate.tables[0].columns if c.name != "revenue"]
    assert validate_spec(dashboard, model()) == []
    errors = validate_spec(dashboard, candidate)
    assert errors == ["chart 'kpi': unknown measure column 'revenue' in dm.sales_daily"]
    report, store = run({REF: dashboard}, candidate_model=candidate)
    assert_check(report, store, invalid=[{"dashboard": REF, "errors": errors}], compiled=[])


@pytest.mark.parametrize("target", ["superset", "datalens"])
def test_valid_dashboard_keeps_head_payloads(target: str) -> None:
    dashboard = spec(target)
    assert validate_spec(dashboard, model()) == []
    report, store = run({REF: dashboard})
    check = next(c for c in report.checks if c.check_id == "bi.contract")
    assert check.status.value == "pass"
    assert_digests(report, store, target)
    assert_check(report, store, invalid=[], compiled=[REF], status="pass")


@pytest.mark.parametrize("target", ["superset", "datalens"])
def test_valid_next_to_invalid_keeps_payloads(target: str) -> None:
    invalid = spec(target, column="ghost_revenue")
    report, store = run({REF: spec(target), SECOND_REF: invalid}, two=True)
    assert_check(
        report,
        store,
        invalid=[{"dashboard": SECOND_REF, "errors": validate_spec(invalid, model())}],
        compiled=[REF],
    )
    assert_digests(report, store, target)


def test_invalid_outranks_missing() -> None:
    invalid = spec(column="ghost_revenue")
    report, store = run({REF: invalid}, two=True)
    assert_check(
        report,
        store,
        invalid=[{"dashboard": REF, "errors": validate_spec(invalid, model())}],
        compiled=[],
        missing=[SECOND_REF],
    )


def test_evidence_is_deterministic_in_delivery_and_validator_order() -> None:
    dashboards = {
        SECOND_REF: spec(table="dm.ghost"),
        REF: spec(column="ghost_revenue", filter_column="dm.sales_daily.ghost"),
    }
    first, first_store = run(dashboards, two=True)
    second, second_store = run(dashboards, two=True)
    invalid = [
        {"dashboard": ref, "errors": validate_spec(dashboards[ref], model())}
        for ref in (REF, SECOND_REF)
    ]
    for report, store in [(first, first_store), (second, second_store)]:
        assert_check(report, store, invalid=invalid, compiled=[])
    assert first_store.get(first.run_id, CHECK_PATH) == second_store.get(second.run_id, CHECK_PATH)


def test_valid_operator_sql_compiles() -> None:
    dashboard = spec(raw_sql="SELECT region, revenue FROM dm.sales_daily")
    assert validate_spec(dashboard, model()) == []
    report, store = run({REF: dashboard})
    expected = release_gate._compile(dashboard, model()).model_dump(mode="json")
    payload = assert_check(report, store, invalid=[], compiled=[REF], status="pass")
    assert payload["compiled"] == [{"dashboard": REF, **expected}]


@pytest.mark.parametrize("target", ["superset", "datalens"])
@pytest.mark.parametrize(
    ("column", "filter_type"),
    [("dm.sales_daily.region", "select"), ("dm.sales_daily.date", "time_range")],
)
def test_valid_qualified_filter_keeps_compiler_findings(
    target: str, column: str, filter_type: str
) -> None:
    dashboard = spec(target, dimension="region", filter_column=column, filter_type=filter_type)
    assert validate_spec(dashboard, model()) == []
    expected = release_gate._compile(dashboard, model()).model_dump(mode="json")
    report, store = run({REF: dashboard})
    payload = assert_check(
        report,
        store,
        invalid=[],
        compiled=[REF],
        status="advisory" if target == "datalens" and filter_type == "time_range" else "pass",
    )
    assert payload["compiled"] == [{"dashboard": REF, **expected}]


def test_missing_spec_keeps_review() -> None:
    report, store = run({})
    assert_check(report, store, invalid=[], compiled=[], missing=[REF], status="review")
