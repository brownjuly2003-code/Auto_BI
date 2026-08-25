"""One-shot release gate scenarios (DECISIONOPS_ARCHITECTURE §8.2, §15.3, bootstrap task 7).

The whole gate runs on files in a tmp dir: contracts, two semantic refs, the delivered
dashboard spec, one verified asset and its recorded candidate result. That is the point —
a release gate nobody can run offline is a release gate nobody runs.

The scenarios §15.3 asks for: four breaking seeds block with exact impact, a
presentation-only change does not block, and the run touches no BI and no DWH.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.use_cases import VerifyError, VerifyRequest, verify
from auto_bi.evidence.manifest import read_bundle, verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    DataClassification,
    Join,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    RoleFacts,
    TableFacts,
)
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus, GateReport, RunStatus

CREATED_AT = "2026-08-25T09:00:00Z"
PRODUCT_ID = "decision://sales/weekly_margin"
ASSET_ID = "asset://sales/weekly_margin_by_region"


# --- fixtures on disk ---------------------------------------------------------


def semantic_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                description="Дневные продажи",
                grain=["date", "store_id"],
                classification=DataClassification.INTERNAL,
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(
                        name="store_id", type="UInt32", role=ColumnRole.DIMENSION, fk="dm.stores.id"
                    ),
                    Column(name="region", type="String", role=ColumnRole.DIMENSION),
                    Column(name="category", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                        description="Выручка, руб",
                    ),
                    Column(
                        name="gross_margin",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(
                    engine="clickhouse",
                    table_engine="MergeTree",
                    sorting_key=["date", "store_id"],
                    partition_key="toYYYYMM(date)",
                ),
            ),
            Table(
                name="dm.stores",
                grain=["id"],
                columns=[Column(name="id", type="UInt32", role=ColumnRole.DIMENSION)],
                physical=Physical(engine="clickhouse"),
            ),
        ],
        joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
    )


def metric_contract() -> dict[str, Any]:
    return {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "MetricContract",
        "metadata": {
            "id": "metric://sales/gross_margin_pct",
            "version": 3,
            "owner": "commercial-analytics",
            "status": "active",
        },
        "formula": {
            "kind": "typed_ratio",
            "numerator": {"table": "dm.sales_daily", "column": "gross_margin", "agg": "sum"},
            "denominator": {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"},
        },
        "grain": {"table": "dm.sales_daily", "keys": ["date", "store_id"]},
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


def product_contract() -> dict[str, Any]:
    return {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "DecisionProduct",
        "metadata": {
            "id": PRODUCT_ID,
            "version": 1,
            "owner": "commercial-analytics",
            "risk_tier": "medium",
        },
        "decision": {
            "question": "Почему изменилась валовая маржа?",
            "consumer": "commercial-director",
            "cadence": "weekly",
        },
        "scope": {
            "metrics": ["metric://sales/gross_margin_pct@3"],
            "dimensions": ["region", "category"],
            "time_window": "P13W",
        },
        "verification": {
            "policy": "policy://release/default@1",
            "freshness_max_age": "PT6H",
            "cross_surface_tolerance": "0.000001",
        },
        "delivery": {"dashboards": [{"ref": "dashboard://weekly_margin@7"}]},
        "actions": {"mode": "disabled"},
    }


def dashboard_spec() -> dict[str, Any]:
    return {
        "title": "Недельная маржа",
        "target_bi": "superset",
        "filters": [{"column": "dm.sales_daily.date"}],
        "charts": [
            {
                "id": "revenue_by_region",
                "title": "Выручка по регионам",
                "viz": "bar",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["region"],
                    "measures": [{"column": "revenue", "agg": "sum"}],
                },
            }
        ],
    }


RESULT_COLUMNS = [
    {"name": "region", "type": "string", "nullable": False},
    {"name": "gross_margin_pct", "type": "decimal", "unit": "ratio"},
]


def verified_asset(semantic_digest: str) -> dict[str, Any]:
    return {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "VerifiedAsset",
        "metadata": {
            "id": ASSET_ID,
            "version": 2,
            "owner": "commercial-analytics",
            "risk_tier": "medium",
        },
        "question": "Какая валовая маржа по регионам за 13 недель?",
        "intent": "breakdown",
        "metric_refs": ["metric://sales/gross_margin_pct@3"],
        "expectation": {
            "kind": "result",
            "result": {
                "columns": RESULT_COLUMNS,
                "rows": [["Москва", "0.412"], ["Казань", "0.377"]],
            },
        },
        "tolerance": {"business_key": ["region"]},
        "semantic_digest": semantic_digest,
        "review": {
            "reviewer": "commercial-analytics",
            "reviewed_at": "2026-08-20T10:00:00Z",
            "rationale": "сверено с закрытым периодом",
        },
    }


def recorded_results(rows: list[list[str]]) -> dict[str, Any]:
    return {ASSET_ID: {"columns": RESULT_COLUMNS, "rows": rows}}


def _write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _write_semantic(root: Path, model: SemanticModel, contract: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    model.dump(root / "model.yaml")
    _write_yaml(root / "metrics" / "gross_margin_pct.yaml", contract)
    return root


class Case:
    """A gate run laid out on disk, so a test only says what it changed."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.contracts = tmp_path / "contracts"
        _write_yaml(self.contracts / "weekly_margin.yaml", product_contract())
        _write_yaml(self.contracts / "gross_margin_pct.yaml", metric_contract())
        self.base = _write_semantic(tmp_path / "base", semantic_model(), metric_contract())
        self.candidate = tmp_path / "candidate"
        self.dashboards = tmp_path / "dashboards"
        _write_yaml(self.dashboards / "weekly_margin@7.yaml", dashboard_spec())
        self.assets: Path | None = None
        self.results: Path | None = None

    @property
    def base_digest(self) -> str:
        return (
            NativeModelSource(self.base / "model.yaml", self.base / "metrics")
            .snapshot()
            .canonical_digest
        )

    def with_candidate(
        self, model: SemanticModel | None = None, contract: dict[str, Any] | None = None
    ) -> Case:
        _write_semantic(self.candidate, model or semantic_model(), contract or metric_contract())
        return self

    def with_asset(self, semantic_digest: str | None = None) -> Case:
        self.assets = self.root / "assets"
        _write_yaml(
            self.assets / "weekly_margin_by_region.yaml",
            verified_asset(semantic_digest or self.base_digest),
        )
        return self

    def with_results(self, rows: list[list[str]]) -> Case:
        self.results = self.root / "results.yaml"
        _write_yaml(self.results, recorded_results(rows))
        return self

    def run(self, **overrides: Any) -> GateReport:
        if not self.candidate.exists():  # default: the candidate is the base, unchanged
            self.with_candidate()
        request = VerifyRequest(
            contract_paths=[self.contracts],
            base=self.base,
            candidate=self.candidate,
            evidence_dir=self.root / "evidence",
            created_at=CREATED_AT,
            dashboards_dir=self.dashboards,
            assets_dir=self.assets,
            results_file=self.results,
            **overrides,
        )
        return verify(request)


def status_of(report: GateReport, check_id: str) -> CheckStatus:
    return next(check.status for check in report.checks if check.check_id == check_id)


def summary_of(report: GateReport, check_id: str) -> str:
    return next(check.summary_code for check in report.checks if check.check_id == check_id)


# --- the happy path -----------------------------------------------------------


def test_unchanged_semantics_with_a_reconciled_asset_does_not_block(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.4120"], ["Казань", "0.377"]])
    report = case.run()

    assert report.operational_status is RunStatus.COMPLETED
    assert report.verdict is Verdict.PASS_WITH_ADVISORY  # execution + the uncompiled ratio
    assert status_of(report, "semantic.diff") is CheckStatus.PASS
    assert status_of(report, "assets.invalidated") is CheckStatus.PASS
    # `0.4120` and `0.412` are one decimal, not two numbers
    assert status_of(report, "results.reconcile") is CheckStatus.PASS
    assert status_of(report, "bi.contract") is CheckStatus.PASS
    assert status_of(report, "evidence.bundle") is CheckStatus.PASS


def test_the_gate_says_which_steps_it_did_not_run(tmp_path: Path) -> None:
    """A clean bundle must not read as a full verification (invariant 6)."""
    report = Case(tmp_path).run()
    not_implemented = [
        check.check_id for check in report.checks if check.summary_code == "check.not_implemented"
    ]
    assert not_implemented == ["execution.checks"]
    assert all(status_of(report, check) is CheckStatus.ADVISORY for check in not_implemented)


# --- §8.2 step 7: what a live engine says -------------------------------------


def environment(
    *,
    read_only: bool = True,
    max_event_time: str | None = "2026-08-25T06:00:00Z",
    rows_seen: int = 600,
    exists: bool = True,
) -> EnvironmentFacts:
    """One probe's answer, as `environment_probe` would have built it from the stand."""
    return EnvironmentFacts(
        engine="clickhouse",
        target="127.0.0.1:8124/dm",
        role=RoleFacts(
            name="gate_ro",
            read_only=read_only,
            evidence="readonly=1" if read_only else "readonly=0, granted INSERT",
        ),
        tables=[
            TableFacts(
                table="dm.sales_daily",
                exists=exists,
                readable=exists,
                rows_seen=rows_seen,
                max_event_time=max_event_time,
            )
        ],
    )


def probing(facts: EnvironmentFacts) -> Callable[[EnvironmentQuestions], EnvironmentFacts]:
    return lambda questions: facts


def test_without_an_engine_step_seven_says_it_was_not_configured(tmp_path: Path) -> None:
    """An implemented check with nothing to point it at is not a pass, and not the same
    answer as a check that does not exist."""
    report = Case(tmp_path).run()

    assert summary_of(report, "dwh.access") == "check.not_configured"
    assert summary_of(report, "dwh.freshness") == "check.not_configured"
    assert status_of(report, "dwh.access") is CheckStatus.ADVISORY


def test_a_read_only_role_on_a_fresh_mart_passes_both_halves(tmp_path: Path) -> None:
    report = Case(tmp_path).run(probe=probing(environment()))

    assert status_of(report, "dwh.access") is CheckStatus.PASS
    assert summary_of(report, "dwh.access") == "access.read_only"
    assert status_of(report, "dwh.freshness") is CheckStatus.PASS
    assert summary_of(report, "dwh.freshness") == "freshness.fresh"


def test_a_role_that_can_write_blocks_the_release(tmp_path: Path) -> None:
    """Invariant 3: evidence gathered through a role that could have changed the mart is
    not the evidence the bundle claims to hold."""
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.4120"], ["Казань", "0.377"]])

    graded = case.run(probe=probing(environment(read_only=False)))

    assert summary_of(graded, "dwh.access") == "access.role_writable"
    assert graded.verdict is Verdict.BLOCK


def test_a_stale_mart_asks_for_a_human_and_does_not_block(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.4120"], ["Казань", "0.377"]])

    report = case.run(probe=probing(environment(max_event_time="2026-08-01T06:00:00Z")))

    assert summary_of(report, "dwh.freshness") == "freshness.stale"
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_an_absent_mart_blocks_and_leaves_freshness_unsaid(tmp_path: Path) -> None:
    """One problem, one finding: freshness has nothing to add about a table that is not
    there, and saying it twice would weigh one fact twice in the verdict."""
    report = Case(tmp_path).run(probe=probing(environment(exists=False)))

    assert summary_of(report, "dwh.access") == "access.table_absent"
    assert summary_of(report, "dwh.freshness") == "freshness.not_run"


def test_the_bundle_carries_the_readings_the_verdict_stood_on(tmp_path: Path) -> None:
    case = Case(tmp_path)
    report = case.run(probe=probing(environment()))

    payload = _check_payload(tmp_path, report, "dwh.freshness")
    assert payload["facts"]["target"] == "127.0.0.1:8124/dm"
    assert payload["facts"]["tables"][0]["max_event_time"] == "2026-08-25T06:00:00Z"
    assert payload["questions"]["tables"][0]["event_time_column"] == "date"


def test_the_same_environment_does_not_move_the_run_id(tmp_path: Path) -> None:
    """The readings are observations of a moment; keying the run on them would make every
    verification a new one and idempotency (§7.6) meaningless."""
    first = Case(tmp_path / "a").run(probe=probing(environment()))
    second = Case(tmp_path / "b").run(
        probe=probing(environment(max_event_time="2026-08-25T06:30:00Z"))
    )

    assert first.run_id == second.run_id


def test_a_product_with_no_verified_asset_is_not_a_clean_pass(tmp_path: Path) -> None:
    """Nothing was compared, so nothing was proven — that is advisory, not a pass."""
    report = Case(tmp_path).run()
    assert summary_of(report, "results.reconcile") == "result.no_expectations"
    assert status_of(report, "results.reconcile") is CheckStatus.ADVISORY


# --- four breaking seeds ------------------------------------------------------


def _formula_change() -> dict[str, Any]:
    contract = metric_contract()
    contract["formula"]["numerator"]["column"] = "revenue"
    return contract


def _grain_change() -> dict[str, Any]:
    contract = metric_contract()
    contract["grain"]["keys"] = ["date"]
    return contract


def _timezone_change() -> dict[str, Any]:
    contract = metric_contract()
    contract["time"]["timezone"] = "UTC"
    return contract


def _removed_field() -> SemanticModel:
    model = semantic_model()
    sales = model.tables[0]
    sales.columns = [column for column in sales.columns if column.name != "gross_margin"]
    return model


def _check_payload(tmp_path: Path, report: GateReport, check_id: str) -> dict[str, Any]:
    path = tmp_path / "evidence" / report.run_id / "checks" / f"check-{check_id}.json"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


@pytest.mark.parametrize(
    ("seed", "model", "contract"),
    [
        ("formula", None, _formula_change()),
        ("grain", None, _grain_change()),
        ("event time", None, _timezone_change()),
        ("removed field", _removed_field(), None),
    ],
)
def test_breaking_seed_blocks_with_exact_impact(
    tmp_path: Path, seed: str, model: SemanticModel | None, contract: dict[str, Any] | None
) -> None:
    report = Case(tmp_path).with_candidate(model, contract).run()

    assert report.operational_status is RunStatus.COMPLETED, seed
    assert report.verdict is Verdict.BLOCK, seed
    assert "impact.products" in {check.check_id for check in report.blocking()}, seed

    impact = _check_payload(tmp_path, report, "impact.products")
    reached = [item for item in impact["impacts"] if item["severity"] == "block"]
    assert reached, seed
    assert {ref for item in reached for ref in item["products"]} == {f"{PRODUCT_ID}@1"}, seed
    assert {owner for item in reached for owner in item["owners"]} == {"commercial-analytics"}, seed
    assert {ref for item in reached for ref in item["dashboards"]} == {
        "dashboard://weekly_margin@7"
    }, seed


@pytest.mark.parametrize(
    ("seed", "contract"),
    [
        ("formula", _formula_change()),
        ("grain", _grain_change()),
        ("event time", _timezone_change()),
    ],
)
def test_a_contract_seed_blocks_only_because_the_product_is_governed(
    tmp_path: Path, seed: str, contract: dict[str, Any]
) -> None:
    """§8.3: these are questions until something depends on them, then they are stoppers."""
    report = Case(tmp_path).with_candidate(contract=contract).run()
    assert status_of(report, "semantic.diff") is CheckStatus.REVIEW, seed
    assert status_of(report, "impact.products") is CheckStatus.BLOCK, seed


def test_removed_field_blocks_as_a_gap_and_keeps_reporting_impact(tmp_path: Path) -> None:
    """The graph still says what it knows, so the owner still learns what broke.

    A field a metric depends on is gone: that is a blocking gap *and* a blocking diff. The
    sequence continues because the projection is still truthful — only an unparsed contract
    makes the diff fiction.
    """
    report = Case(tmp_path).with_candidate(_removed_field()).run()
    blocking = {check.check_id for check in report.blocking()}
    assert {"semantic.gaps", "semantic.diff", "impact.products"} <= blocking

    gaps = _check_payload(tmp_path, report, "semantic.gaps")
    assert [gap["code"] for gap in gaps["candidate"]] == ["gap.metric_field_missing"]
    assert gaps["base"] == []
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert "field.removed" in {change["code"] for change in diff["changes"]}


def test_breaking_seed_invalidates_the_verified_asset(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change()).with_asset()
    report = case.run()
    assert status_of(report, "assets.invalidated") is CheckStatus.REVIEW
    assert summary_of(report, "assets.invalidated") == "assets.invalidated"


# --- presentation-only --------------------------------------------------------


def test_presentation_only_change_does_not_block(tmp_path: Path) -> None:
    """A relabelled column is advisory: nothing about a number moved."""
    model = semantic_model()
    model.tables[0].columns[4].description = "Выручка с НДС, руб"
    report = (
        Case(tmp_path)
        .with_candidate(model)
        .with_asset()
        .with_results([["Москва", "0.412"], ["Казань", "0.377"]])
        .run()
    )
    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert status_of(report, "semantic.diff") is CheckStatus.ADVISORY
    assert status_of(report, "assets.invalidated") is CheckStatus.PASS
    assert report.blocking() == []


# --- honest gaps --------------------------------------------------------------


def test_missing_candidate_result_is_review_not_pass(tmp_path: Path) -> None:
    """A comparison that did not happen is not evidence that nothing moved."""
    report = Case(tmp_path).with_asset().run()
    assert status_of(report, "results.reconcile") is CheckStatus.REVIEW
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_stale_asset_is_not_evidence(tmp_path: Path) -> None:
    other = "sha256:" + "a" * 64
    report = Case(tmp_path).with_asset(semantic_digest=other).run()
    assert status_of(report, "assets.invalidated") is CheckStatus.REVIEW


def test_missing_dashboard_spec_is_reported(tmp_path: Path) -> None:
    case = Case(tmp_path)
    (case.dashboards / "weekly_margin@7.yaml").unlink()
    report = case.run()
    assert status_of(report, "bi.contract") is CheckStatus.REVIEW
    assert summary_of(report, "bi.contract") == "bi.spec_missing"


def test_a_pinned_dashboard_ref_does_not_load_another_version(tmp_path: Path) -> None:
    """`dashboard://weekly_margin@7` must not resolve to version 8's layout."""
    case = Case(tmp_path)
    (case.dashboards / "weekly_margin@7.yaml").rename(case.dashboards / "weekly_margin@8.yaml")
    assert summary_of(case.run(), "bi.contract") == "bi.spec_missing"


def test_blocking_semantic_gap_stops_the_diff_instead_of_guessing(tmp_path: Path) -> None:
    """A contract that did not parse would look like a removed metric to a diff."""
    case = Case(tmp_path).with_candidate()
    (case.candidate / "metrics" / "broken.yaml").write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/x}\n", encoding="utf-8"
    )
    report = case.run()
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.diff") == "check.not_run"
    assert report.verdict is Verdict.BLOCK


# --- evidence -----------------------------------------------------------------


def test_the_bundle_holds_every_input_the_verdict_used(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run()
    store = FilesystemArtifactStore(tmp_path / "evidence")
    bundle = read_bundle(store, report.run_id)

    assert bundle.product_ref == f"{PRODUCT_ID}@1"
    assert bundle.digest() == report.evidence_digest
    ids = {artifact.artifact_id for artifact in bundle.artifacts}
    assert {"snapshot-base", "snapshot-candidate"} <= ids
    assert {
        "check-contracts.validate",
        "check-semantic.gaps",
        "check-semantic.diff",
        "check-impact.products",
        "check-assets.invalidated",
        "check-results.reconcile",
        "check-bi.contract",
    } <= ids
    # the compiled BI payloads travel as blobs, addressed by content
    assert any(item.startswith("bi-dashboard-weekly-margin-7-") for item in ids)
    assert verify_bundle(store, report.run_id) == []


def test_a_reconciled_result_becomes_a_claim_with_its_evidence(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run()
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    claim = next(item for item in bundle.claims if item.summary_code == "result.equivalent")
    assert claim.evidence_refs == ["check-results.reconcile"]


def test_a_different_result_makes_no_claim(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.500"], ["Казань", "0.377"]])
    report = case.run()
    assert status_of(report, "results.reconcile") is CheckStatus.BLOCK
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    assert bundle.claims == []


def test_the_same_inputs_produce_the_same_run_and_the_same_bundle(tmp_path: Path) -> None:
    """§7.6: the idempotency key is what makes a prior terminal bundle reusable."""
    first = Case(tmp_path / "a").with_asset().with_results([["Москва", "0.412"]]).run()
    second = Case(tmp_path / "b").with_asset().with_results([["Москва", "0.412"]]).run()
    assert first.idempotency_key == second.idempotency_key
    assert first.run_id == second.run_id
    assert first.evidence_digest == second.evidence_digest


def test_a_changed_environment_is_a_different_run(tmp_path: Path) -> None:
    plain = Case(tmp_path / "a").run()
    staged = Case(tmp_path / "b").run(environment_ref="env://staging@1")
    assert plain.idempotency_key != staged.idempotency_key
    assert plain.run_id != staged.run_id


# --- no remote anything -------------------------------------------------------


def test_verify_never_opens_a_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("decision verify must not reach the DWH, the BI or an LLM")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    assert case.run().operational_status is RunStatus.COMPLETED


def test_verify_calls_no_bi_client_method(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auto_bi.adapters.datalens.client import DataLensClient
    from auto_bi.adapters.superset.client import SupersetClient

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("decision verify must not mutate the BI")

    for verb in ("request", "get", "post", "put", "delete", "login"):
        monkeypatch.setattr(SupersetClient, verb, fail)
    for verb in ("post", "gateway", "login"):
        monkeypatch.setattr(DataLensClient, verb, fail)
    assert Case(tmp_path).run().verdict is not None


# --- report invariants --------------------------------------------------------


def test_report_round_trips_and_rejects_a_verdict_its_checks_contradict(
    tmp_path: Path,
) -> None:
    report = Case(tmp_path).run()
    assert GateReport.model_validate(report.model_dump(mode="json")) == report

    lying = report.model_dump(mode="json")
    lying["verdict"] = Verdict.PASS.value
    with pytest.raises(ValueError, match="contradicts the checks"):
        GateReport.model_validate(lying)


def test_an_errored_check_means_the_run_failed(tmp_path: Path) -> None:
    """Invariant 8: a crash must never be dressed up as a graded verdict."""
    report = Case(tmp_path).run()
    broken = report.model_dump(mode="json")
    broken["checks"][0]["status"] = CheckStatus.ERROR.value
    with pytest.raises(ValueError, match="the run failed"):
        GateReport.model_validate(broken)


# --- usage errors -------------------------------------------------------------


def test_no_product_in_the_paths_is_a_usage_error(tmp_path: Path) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    with pytest.raises(VerifyError, match="no decision product"):
        case.run()


def test_two_products_need_an_explicit_choice(tmp_path: Path) -> None:
    case = Case(tmp_path)
    other = product_contract()
    other["metadata"]["id"] = "decision://sales/monthly_margin"
    _write_yaml(case.contracts / "monthly_margin.yaml", other)
    with pytest.raises(VerifyError, match="name the one to verify"):
        case.run()
    assert case.run(product_id=PRODUCT_ID).product_ref == f"{PRODUCT_ID}@1"


def test_a_semantic_ref_without_a_model_is_a_usage_error(tmp_path: Path) -> None:
    case = Case(tmp_path)
    case.candidate.mkdir(parents=True, exist_ok=True)
    with pytest.raises(VerifyError, match=r"has no model\.yaml"):
        case.run()
