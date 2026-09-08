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
    PlanFacts,
    RoleFacts,
    RowPolicyFacts,
    TableFacts,
)
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus, GateReport, RunStatus
from auto_bi.verification.repository import ReferenceQuery

CREATED_AT = "2026-08-25T09:00:00Z"
ENVIRONMENT_REF = "clickhouse-stage:9000/dm"
PRODUCT_ID = "decision://sales/weekly_margin"
ASSET_ID = "asset://sales/weekly_margin_by_region"


class _Unset:
    """Distinguishes "no attestation" from "the default attestation" in `with_results`."""


_UNSET = _Unset()


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


#: The reviewed SQL the asset's answer comes from. Its digest is what a candidate result
#: attests to, so a test that changes one character here changes what provenance proves.
REFERENCE_SQL = (
    'SELECT "region", sum("revenue") AS "gross_margin_pct" '
    'FROM "dm"."sales_daily" GROUP BY "region"'
)


def reference_query_digest() -> str:
    return ReferenceQuery(engine="clickhouse", sql=REFERENCE_SQL).digest()


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
        "reference_query": {"engine": "clickhouse", "sql": REFERENCE_SQL},
        "semantic_digest": semantic_digest,
        "review": {
            "reviewer": "commercial-analytics",
            "reviewed_at": "2026-08-20T10:00:00Z",
            "rationale": "сверено с закрытым периодом",
        },
    }


def attestation(candidate_digest: str, **overrides: Any) -> dict[str, Any]:
    """A capture that establishes provenance; a test overrides the one field it is about."""
    return {
        "origin": "query_run",
        "captured_at": CREATED_AT,
        "environment_ref": ENVIRONMENT_REF,
        "semantic_digest": candidate_digest,
        "query_digest": reference_query_digest(),
        "executed_by": "bi_reader",
        **overrides,
    }


def recorded_results(rows: list[list[str]], attested: dict[str, Any] | None) -> dict[str, Any]:
    result = {"columns": RESULT_COLUMNS, "rows": rows}
    if attested is None:
        # The shape a results file had before attestations existed, still readable.
        return {ASSET_ID: result}
    return {ASSET_ID: {"result": result, "attestation": attested}}


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

    def _ensure_candidate(self) -> None:
        """Default: the candidate is the base, unchanged. Written on first need."""
        if not self.candidate.exists():
            self.with_candidate()

    @property
    def candidate_digest(self) -> str:
        self._ensure_candidate()
        return (
            NativeModelSource(self.candidate / "model.yaml", self.candidate / "metrics")
            .snapshot()
            .canonical_digest
        )

    def with_results(
        self, rows: list[list[str]], attested: dict[str, Any] | _Unset | None = _UNSET
    ) -> Case:
        """Record a candidate answer. Attested against this candidate by default.

        `attested=None` writes the bare pre-attestation shape; a dict replaces the whole
        attestation, so a test that is about one wrong field says only that field through
        `attestation(...)`.
        """
        self.results = self.root / "results.yaml"
        if isinstance(attested, _Unset):
            attested = attestation(self.candidate_digest)
        _write_yaml(self.results, recorded_results(rows, attested))
        return self

    def run(self, **overrides: Any) -> GateReport:
        self._ensure_candidate()
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


def detail_of(report: GateReport, check_id: str) -> str:
    return next(check.detail for check in report.checks if check.check_id == check_id)


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
    """A clean bundle must not read as a full verification (invariant 6).

    Step 7 is implemented end to end now, so nothing is `check.not_implemented`; what an
    offline run cannot do is *ask*, and every one of its five checks says exactly that.
    """
    report = Case(tmp_path).run()

    assert [
        check.check_id for check in report.checks if check.summary_code == "check.not_implemented"
    ] == []
    not_configured = [
        check.check_id for check in report.checks if check.summary_code == "check.not_configured"
    ]
    assert not_configured == [
        "dwh.access",
        "dwh.freshness",
        "dwh.physical",
        "dwh.security",
        "dwh.cost",
    ]
    assert all(status_of(report, check) is CheckStatus.ADVISORY for check in not_configured)


# --- §8.2 step 7: what a live engine says -------------------------------------


#: The columns the in-scope metric is made of, all of them in reach of the read-only role.
IN_REACH = {"date": True, "store_id": True, "revenue": True, "gross_margin": True}


def environment(
    *,
    read_only: bool = True,
    max_event_time: str | None = "2026-08-25T06:00:00Z",
    rows_seen: int = 600,
    exists: bool = True,
    column_reads: dict[str, bool] | None = None,
    row_policies: list[RowPolicyFacts] | None = None,
    row_security_enabled: bool | None = None,
    plans: list[PlanFacts] | None = None,
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
                column_reads=dict(IN_REACH) if column_reads is None else column_reads,
                row_policies=[] if row_policies is None else row_policies,
                row_security_enabled=row_security_enabled,
            )
        ],
        plans=plans or [],
    )


def probing(facts: EnvironmentFacts) -> Callable[[EnvironmentQuestions], EnvironmentFacts]:
    return lambda questions: facts


def test_without_an_engine_step_seven_says_it_was_not_configured(tmp_path: Path) -> None:
    """An implemented check with nothing to point it at is not a pass, and not the same
    answer as a check that does not exist."""
    report = Case(tmp_path).run()

    assert summary_of(report, "dwh.access") == "check.not_configured"
    assert summary_of(report, "dwh.freshness") == "check.not_configured"
    assert summary_of(report, "dwh.security") == "check.not_configured"
    assert status_of(report, "dwh.access") is CheckStatus.ADVISORY


def test_a_role_nothing_narrows_passes_the_security_half(tmp_path: Path) -> None:
    """Step 7's security half is a check, not a promise: it passes only by saying what it
    established — no policy filters this session and every column is in reach."""
    report = Case(tmp_path).run(probe=probing(environment()))

    assert status_of(report, "dwh.security") is CheckStatus.PASS
    assert summary_of(report, "dwh.security") == "security.unfiltered"


def test_a_row_policy_makes_the_readings_partial_and_asks_for_a_human(tmp_path: Path) -> None:
    """The numbers are real, they are just not the mart's: whether a decision product may
    stand on a filtered reading is the owner's call, so it is review and not a block."""
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.4120"], ["Казань", "0.377"]])

    report = case.run(
        probe=probing(
            environment(
                row_policies=[
                    RowPolicyFacts(
                        name="tenant_pol",
                        applies_to_session=True,
                        expression="store_id = 1",
                    )
                ]
            )
        )
    )

    assert summary_of(report, "dwh.security") == "security.row_filtered"
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_a_column_the_role_may_not_read_blocks_the_release(tmp_path: Path) -> None:
    report = Case(tmp_path).run(
        probe=probing(environment(column_reads={**IN_REACH, "gross_margin": False}))
    )

    assert summary_of(report, "dwh.security") == "security.column_unreadable"
    assert report.verdict is Verdict.BLOCK


def test_a_mart_row_security_empties_is_never_reported_as_fresh(tmp_path: Path) -> None:
    """The one sentence this report must not contain is "every mart is inside the window"
    about a mart nothing was read from — and row security empties one without an error."""
    report = Case(tmp_path).run(
        probe=probing(
            environment(
                rows_seen=0,
                max_event_time=None,
                row_security_enabled=True,
                row_policies=[
                    RowPolicyFacts(
                        name="r", applies_to_session=True, restrictive=True, expression="false"
                    )
                ],
            )
        )
    )

    assert summary_of(report, "dwh.security") == "security.row_policy_denies_all"
    assert summary_of(report, "dwh.freshness") == "freshness.not_run"
    assert report.verdict is Verdict.BLOCK


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
    """The check names *why* the asset fell: two reasons need two different fixes."""
    case = Case(tmp_path).with_candidate(contract=_formula_change()).with_asset()
    report = case.run()
    assert status_of(report, "assets.invalidated") is CheckStatus.REVIEW
    assert summary_of(report, "assets.invalidated") == "asset.metric_changed"
    assert ASSET_ID in detail_of(report, "assets.invalidated")


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
    """The comparison AND the attestation of the number it compared (invariant 5)."""
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run()
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    claim = next(item for item in bundle.claims if item.summary_code == "result.equivalent")
    assert claim.evidence_refs == ["check-results.reconcile", "check-results.provenance"]
    assert claim.caveats == [f"verified against snapshot {case.base_digest}"]


def test_a_different_result_makes_no_claim(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.500"], ["Казань", "0.377"]])
    report = case.run()
    assert status_of(report, "results.reconcile") is CheckStatus.BLOCK
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    assert bundle.claims == []


# --- where the candidate number came from (§8.4) -------------------------------


def test_an_attested_capture_of_this_release_establishes_its_provenance(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run(environment_ref=ENVIRONMENT_REF)
    assert status_of(report, "results.provenance") is CheckStatus.PASS
    assert summary_of(report, "results.provenance") == "provenance.established"
    payload = _check_payload(tmp_path, report, "results.provenance")
    assert payload["results"][0]["findings"] == []
    assert payload["results"][0]["reviewed_query_digest"] == reference_query_digest()


def test_a_supplied_number_with_no_attestation_is_not_a_verified_one(tmp_path: Path) -> None:
    """The whole point: the numbers reconcile, and nobody can say where the second came from."""
    case = (
        Case(tmp_path)
        .with_asset()
        .with_results([["Москва", "0.412"], ["Казань", "0.377"]], attested=None)
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    assert summary_of(report, "results.reconcile") == "result.equivalent"
    assert status_of(report, "results.provenance") is CheckStatus.REVIEW
    # The summary code names the finding that decided it, so an owner reading the report
    # learns what is missing without opening the artifact.
    assert summary_of(report, "results.provenance") == "provenance.unattested"
    assert report.verdict is Verdict.NEEDS_REVIEW
    assert report.blocking() == []  # a question for the owner, never a block


def test_an_unproven_number_carries_its_reason_into_the_claim(tmp_path: Path) -> None:
    """A `result.equivalent` observation read outside the bundle must not look clean."""
    case = (
        Case(tmp_path)
        .with_asset()
        .with_results([["Москва", "0.412"], ["Казань", "0.377"]], attested=None)
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    claim = next(item for item in bundle.claims if item.summary_code == "result.equivalent")
    assert any(caveat.startswith("provenance.unattested:") for caveat in claim.caveats)
    assert "check-results.provenance" in claim.evidence_refs


def test_a_number_from_a_query_nobody_reviewed_is_not_this_products_evidence(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_asset()
    case.with_results(
        [["Москва", "0.412"], ["Казань", "0.377"]],
        attested=attestation(case.candidate_digest, query_digest="sha256:" + "9" * 64),
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    payload = _check_payload(tmp_path, report, "results.provenance")
    assert [item["code"] for item in payload["results"][0]["findings"]] == [
        "provenance.other_query"
    ]
    assert status_of(report, "results.provenance") is CheckStatus.REVIEW


def test_a_number_captured_on_another_environment_is_named(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset()
    case.with_results(
        [["Москва", "0.412"], ["Казань", "0.377"]],
        attested=attestation(case.candidate_digest, environment_ref="clickhouse-prod:9000/dm"),
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    payload = _check_payload(tmp_path, report, "results.provenance")
    assert [item["code"] for item in payload["results"][0]["findings"]] == [
        "provenance.other_environment"
    ]


def test_a_number_captured_against_the_base_does_not_describe_the_candidate(
    tmp_path: Path,
) -> None:
    """A capture that predates the change reconciles perfectly and proves nothing about it."""
    case = Case(tmp_path).with_candidate(contract=_timezone_change()).with_asset()
    case.with_results(
        [["Москва", "0.412"], ["Казань", "0.377"]],
        attested=attestation(case.base_digest),
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    payload = _check_payload(tmp_path, report, "results.provenance")
    assert [item["code"] for item in payload["results"][0]["findings"]] == [
        "provenance.other_snapshot"
    ]


def test_an_asset_with_no_reviewed_query_says_so_without_asking_for_a_human(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_asset()
    asset = verified_asset(case.base_digest)
    del asset["reference_query"]
    _write_yaml(case.assets / "weekly_margin_by_region.yaml", asset)
    case.with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    report = case.run(environment_ref=ENVIRONMENT_REF)
    assert status_of(report, "results.provenance") is CheckStatus.ADVISORY
    payload = _check_payload(tmp_path, report, "results.provenance")
    assert [item["code"] for item in payload["results"][0]["findings"]] == [
        "provenance.query_unreviewed"
    ]


def test_a_reviewed_query_over_an_ungoverned_mart_cannot_anchor_the_chain(
    tmp_path: Path,
) -> None:
    """The number is attested, reconciles, and reads a mart this model does not hold."""
    case = Case(tmp_path).with_candidate()
    sql = 'SELECT "region", sum("revenue") AS "gross_margin_pct" FROM "dm"."sales_daily_v2"'
    asset = verified_asset(case.base_digest)
    asset["reference_query"] = {"engine": "clickhouse", "sql": sql}
    case.assets = case.root / "assets"
    _write_yaml(case.assets / "weekly_margin_by_region.yaml", asset)
    case.with_results(
        [["Москва", "0.412"], ["Казань", "0.377"]],
        attested=attestation(
            case.candidate_digest,
            query_digest=ReferenceQuery(engine="clickhouse", sql=sql).digest(),
        ),
    )

    report = case.run(environment_ref=ENVIRONMENT_REF)

    # Every link the provenance chain can check holds: the capture names this snapshot,
    # this environment, and exactly the query the asset was reviewed with.
    assert status_of(report, "results.provenance") is CheckStatus.PASS
    assert summary_of(report, "results.reconcile") == "result.equivalent"
    # ...and the chain still never reaches the governed model.
    assert status_of(report, "assets.invalidated") is CheckStatus.REVIEW
    assert summary_of(report, "assets.invalidated") == "asset.query_ungoverned"
    # The owner report carries only the check detail, so the mart has to be named there.
    assert "dm.sales_daily_v2" in detail_of(report, "assets.invalidated")
    payload = _check_payload(tmp_path, report, "assets.invalidated")
    assert [item["code"] for item in payload["invalidations"]] == ["asset.query_ungoverned"]
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_a_missing_result_is_asked_for_once_not_twice(tmp_path: Path) -> None:
    """`results.reconcile` already calls it insufficient; provenance must not weigh it again."""
    case = Case(tmp_path).with_asset()
    report = case.run(environment_ref=ENVIRONMENT_REF)
    assert status_of(report, "results.reconcile") is CheckStatus.REVIEW
    assert status_of(report, "results.provenance") is CheckStatus.ADVISORY
    assert summary_of(report, "results.provenance") == "provenance.no_results"
    assert _check_payload(tmp_path, report, "results.provenance")["results"] == []


def test_a_run_with_no_expectations_at_all_still_writes_the_provenance_artifact(
    tmp_path: Path,
) -> None:
    report = Case(tmp_path).run()
    assert summary_of(report, "results.provenance") == "provenance.no_results"
    assert _check_payload(tmp_path, report, "results.provenance") == {"results": []}


def test_a_wrong_number_blocks_whatever_its_provenance_says(tmp_path: Path) -> None:
    """Provenance never rescues a difference, and never doubles it: one block, one review."""
    case = (
        Case(tmp_path)
        .with_asset()
        .with_results([["Москва", "0.500"], ["Казань", "0.377"]], attested=None)
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    assert status_of(report, "results.reconcile") is CheckStatus.BLOCK
    assert status_of(report, "results.provenance") is CheckStatus.REVIEW
    assert report.verdict is Verdict.BLOCK


def test_an_unparsed_contract_stops_provenance_with_the_rest_of_the_sequence(
    tmp_path: Path,
) -> None:
    """A graph that does not represent its source cannot say which snapshot a number is of."""
    case = Case(tmp_path).with_asset().with_results([["Москва", "0.412"]])
    (case.candidate / "metrics" / "broken.yaml").write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/x}\n", encoding="utf-8"
    )
    report = case.run()
    assert summary_of(report, "results.provenance") == "check.not_run"


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


# --- §8.2 step 7: what one run of a governed query may cost --------------------

BUDGET_REF = "metric://sales/gross_margin_pct@3"


def budgeted_contract(**budgets: int) -> dict[str, Any]:
    return {**metric_contract(), "budgets": budgets}


def estimate(scanned_rows: int | None, evidence: str = "EXPLAIN ESTIMATE: 600 row(s)") -> PlanFacts:
    return PlanFacts(metric_ref=BUDGET_REF, scanned_rows=scanned_rows, evidence=evidence)


def test_a_product_that_declares_no_ceiling_says_so_instead_of_passing(tmp_path: Path) -> None:
    """Nothing was compared, and a green verdict must not read as if something had been."""
    report = Case(tmp_path).run(probe=probing(environment()))

    assert status_of(report, "dwh.cost") is CheckStatus.PASS
    assert summary_of(report, "dwh.cost") == "cost.no_budget_declared"


def test_a_plan_inside_the_ceiling_its_metric_declares_passes(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(max_scanned_rows=1000))

    report = case.run(probe=probing(environment(plans=[estimate(600)])))

    assert status_of(report, "dwh.cost") is CheckStatus.PASS
    assert summary_of(report, "dwh.cost") == "cost.within_budget"


def test_a_plan_over_its_ceiling_asks_for_a_human(tmp_path: Path) -> None:
    """The number stays right; shipping a metric that costs more than its owner declared is
    that owner's call."""
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(max_scanned_rows=500))

    report = case.run(probe=probing(environment(plans=[estimate(600)])))

    assert status_of(report, "dwh.cost") is CheckStatus.REVIEW
    assert summary_of(report, "dwh.cost") == "cost.over_budget"
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_a_ceiling_the_engine_gave_no_estimate_for_is_not_a_pass(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(max_scanned_rows=500))
    silent = estimate(None, evidence="EXPLAIN ESTIMATE named no part to read")

    report = case.run(probe=probing(environment(plans=[silent])))

    assert status_of(report, "dwh.cost") is CheckStatus.REVIEW
    assert summary_of(report, "dwh.cost") == "cost.not_estimated"


def test_the_bundle_carries_the_estimate_the_verdict_stood_on(tmp_path: Path) -> None:
    """A cost finding is only checkable if the reading behind it travels with it."""
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(max_scanned_rows=500))

    report = case.run(probe=probing(environment(plans=[estimate(600)])))
    payload = _check_payload(tmp_path, report, "dwh.cost")

    assert payload["facts"]["plans"] == [
        {
            "metric_ref": BUDGET_REF,
            "scanned_rows": 600,
            "evidence": "EXPLAIN ESTIMATE: 600 row(s)",
        }
    ]
    plan = payload["questions"]["plans"][0]
    assert plan["budgets"]["max_scanned_rows"] == 500
    assert plan["sql"].startswith("SELECT")
    assert [item["code"] for item in payload["findings"]] == ["cost.over_budget"]


def test_a_ceiling_no_planner_can_answer_is_named_without_blocking(tmp_path: Path) -> None:
    """Bytes and a timeout are this gate's boundary, not the owner's, so they read the way
    step 6's boundary does — advisory, and said out loud."""
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(timeout_ms=30_000))

    report = case.run(probe=probing(environment()))

    assert status_of(report, "dwh.cost") is CheckStatus.ADVISORY
    assert summary_of(report, "dwh.cost") == "cost.bound_not_estimable"
    assert report.verdict is Verdict.PASS_WITH_ADVISORY


# --- a change that reaches another product, not this one ----------------------


REVENUE_PRODUCT_ID = "decision://sales/weekly_revenue"


def revenue_metric() -> dict[str, Any]:
    contract = metric_contract()
    contract["metadata"]["id"] = "metric://sales/revenue"
    contract["formula"] = {
        "kind": "typed_aggregate",
        "aggregate": {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"},
    }
    contract["additivity"] = "additive"
    return contract


def revenue_product() -> dict[str, Any]:
    product = product_contract()
    product["metadata"]["id"] = REVENUE_PRODUCT_ID
    product["decision"]["question"] = "Как изменилась выручка?"
    product["scope"]["metrics"] = ["metric://sales/revenue@3"]
    product["delivery"]["dashboards"] = [{"ref": "dashboard://weekly_revenue@1"}]
    return product


def _two_product_graph(case: Case) -> Case:
    """The margin product beside a revenue product that shares the mart, not the metric."""
    revenue = revenue_metric()
    _write_yaml(case.contracts / "revenue.yaml", revenue)
    _write_yaml(case.contracts / "weekly_revenue.yaml", revenue_product())
    for root in (case.base, case.candidate):
        _write_yaml(root / "metrics" / "revenue.yaml", revenue)
    _write_yaml(case.dashboards / "weekly_revenue@1.yaml", dashboard_spec())
    return case


def test_a_change_to_another_products_metric_does_not_reach_this_one(tmp_path: Path) -> None:
    """§8.3: a change is a question for the products that depend on the metric — for the
    product that does not, the impact check has nothing to report, and must not say
    "reaches this product" about a change whose reach is empty."""
    case = _two_product_graph(Case(tmp_path).with_candidate(contract=_timezone_change()))

    report = case.run(product_id=REVENUE_PRODUCT_ID)

    assert status_of(report, "semantic.diff") is CheckStatus.REVIEW  # the change is real
    assert status_of(report, "impact.products") is CheckStatus.PASS
    assert summary_of(report, "impact.products") == "impact.none"
    assert report.blocking() == []
    payload = _check_payload(tmp_path, report, "impact.products")
    assert [item["products"] for item in payload["impacts"]] == [[]]  # still says what it saw
    summary = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    assert "Whose numbers this reaches" not in summary


def _with_margin_tile(case: Case) -> Case:
    """Add the tile that IS the governed ratio — numerator and denominator, term for term."""
    spec = dashboard_spec()
    spec["charts"].append(
        {
            "id": "margin_by_region",
            "title": "Маржа по регионам",
            "viz": "bar",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["region"],
                "measures": [
                    {
                        "column": "gross_margin",
                        "agg": "sum",
                        "denominator": {"column": "revenue", "agg": "sum"},
                    }
                ],
            },
        }
    )
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)
    return case


def test_impact_names_the_tile_that_computes_the_changed_metric(tmp_path: Path) -> None:
    """§8.3 one link further: the owner gets the chart, not just the dashboard it sits on."""
    case = _with_margin_tile(Case(tmp_path)).with_candidate(contract=_formula_change())

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    payload = _check_payload(tmp_path, report, "impact.products")
    charts = [chart for item in payload["impacts"] for chart in item["charts"]]
    assert [(chart["ref"], chart["relation"]) for chart in charts] == [
        ("dashboard://weekly_margin@7#margin_by_region", "computes_metric")
    ]
    assert payload["chart_gaps"] == []

    summary = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    assert "chart `dashboard://weekly_margin@7#margin_by_region` — computes_metric" in summary


def test_a_tile_the_run_cannot_read_is_named_in_the_report(tmp_path: Path) -> None:
    """An operator `raw_sql` tile: the IR does not say what it reads, and silence would lie."""
    case = _with_margin_tile(Case(tmp_path))
    spec = yaml.safe_load((case.dashboards / "weekly_margin@7.yaml").read_text(encoding="utf-8"))
    spec["charts"].append(
        {
            "id": "operator_table",
            "title": "Операторский срез",
            "viz": "table",
            "query": {
                "table": "dm.sales_daily",
                "raw_sql": "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region",
            },
        }
    )
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)

    report = case.with_candidate(contract=_formula_change()).run()

    payload = _check_payload(tmp_path, report, "impact.products")
    assert [(gap["code"], gap["subject"]) for gap in payload["chart_gaps"]] == [
        ("chart.opaque_sql", "dashboard://weekly_margin@7#operator_table")
    ]
    summary = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    assert "Charts this run could not resolve" in summary
    assert "`chart.opaque_sql` dashboard://weekly_margin@7#operator_table" in summary


def test_the_same_change_still_blocks_the_product_that_depends_on_it(tmp_path: Path) -> None:
    case = _two_product_graph(Case(tmp_path).with_candidate(contract=_timezone_change()))

    report = case.run(product_id=PRODUCT_ID)

    assert report.verdict is Verdict.BLOCK
    impact = _check_payload(tmp_path, report, "impact.products")
    assert [item["products"] for item in impact["impacts"]] == [[f"{PRODUCT_ID}@1"]]
    assert "impact.products" in {check.check_id for check in report.blocking()}


def test_an_asset_of_another_products_metric_is_not_this_products_evidence(
    tmp_path: Path,
) -> None:
    """The run verifies one product; another product's asset is neither its evidence nor its
    finding, and a claim about a number it does not deliver must not be sealed into its bundle."""
    case = _two_product_graph(Case(tmp_path).with_candidate(contract=_timezone_change()))
    case.with_asset().with_results([["Москва", "0.412"]])

    report = case.run(product_id=REVENUE_PRODUCT_ID)

    assert summary_of(report, "assets.invalidated") == "assets.valid"
    assert status_of(report, "assets.invalidated") is CheckStatus.PASS
    assert summary_of(report, "results.reconcile") == "result.no_expectations"
    assert _check_payload(tmp_path, report, "assets.invalidated")["assets"] == []
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    assert bundle.claims == []


def test_the_products_own_asset_is_still_reconciled_on_that_graph(tmp_path: Path) -> None:
    case = _two_product_graph(Case(tmp_path).with_candidate())
    case.with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])

    report = case.run(product_id=PRODUCT_ID)

    assert summary_of(report, "results.reconcile") == "result.equivalent"


def test_a_second_verdict_never_overwrites_the_first_bundle(tmp_path: Path) -> None:
    """Same idempotency key, other recorded results: the run does not get to reseal the id."""
    case = Case(tmp_path).with_asset()
    case.with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    first = case.run()

    case.with_results([["Москва", "0.500"], ["Казань", "0.377"]])
    with pytest.raises(VerifyError, match="refusing to overwrite"):
        case.run()

    store = FilesystemArtifactStore(tmp_path / "evidence")
    assert verify_bundle(store, first.run_id) == []
    assert read_bundle(store, first.run_id).run_id == first.run_id
