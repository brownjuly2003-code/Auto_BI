"""Reference SQL for a governed metric (ARCHITECTURE §8.2 step 6).

Step 6 compiles the query a metric *means* — its definition at its own declared grain, not
a chart's windowed, filtered, top-N view of it. Two properties make that worth having in
evidence: a reviewer reading "formula changed" can see the before and after as SQL, and
step 7 later has something concrete to execute.

The boundary is the interesting part. This compiler emits a query only where it can vouch
for the semantics offline:

* a typed aggregate at its grain is exact in both engines — it compiles;
* a governed ratio is not, because decimal division is engine-specific (ClickHouse keeps
  the dividend's scale on `Decimal / Decimal`, and the chart path answers that by casting to
  Float64 — a display decision a governed decimal metric must not inherit). Without a stand
  to verify the exact form, the gate names the gap instead of emitting SQL it cannot stand
  behind;
* an external or reviewed-SQL formula is somebody else's definition and is not re-derived.

Every finding here is advisory: the check carries evidence, and the grading of *why* a
metric moved already happened in the semantic diff. Reporting it twice would let one change
count twice toward a verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from auto_bi.agent import sqlgen
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.semantic.model import SemanticModel, Table
from auto_bi.severity import Severity
from auto_bi.verification import sql_contract
from auto_bi.verification.sql_contract import compile_metric_queries
from tests.test_decision_verify import metric_contract, semantic_model

RATIO_REF = "metric://sales/gross_margin_pct@3"
REVENUE_REF = "metric://sales/revenue@3"


def aggregate_contract(**overrides: Any) -> dict[str, Any]:
    """The same contract shape, but a plain additive aggregate."""
    contract = metric_contract()
    contract["metadata"]["id"] = "metric://sales/revenue"
    contract["formula"] = {
        "kind": "typed_aggregate",
        "aggregate": {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"},
    }
    contract["additivity"] = "additive"
    contract["unit"] = {
        "unit": "RUB",
        "precision": 2,
        "null_semantics": "null_is_missing",
        "zero_semantics": "zero_is_value",
    }
    contract.update(overrides)
    return contract


def snapshot_of(
    root: Path, *contracts: dict[str, Any], model: SemanticModel | None = None
) -> SemanticSnapshot:
    root.mkdir(parents=True, exist_ok=True)
    (model or semantic_model()).dump(root / "model.yaml")
    metrics = root / "metrics"
    metrics.mkdir(exist_ok=True)
    for contract in contracts:
        name = contract["metadata"]["id"].rsplit("/", 1)[-1]
        metrics.joinpath(f"{name}.yaml").write_text(
            yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return NativeModelSource(root / "model.yaml", metrics).snapshot()


# --- what compiles ------------------------------------------------------------


def test_a_typed_aggregate_compiles_at_its_own_grain(tmp_path: Path) -> None:
    compiled = compile_metric_queries(snapshot_of(tmp_path, aggregate_contract()), [REVENUE_REF])

    assert compiled.findings == []
    query = compiled.queries[0]
    assert query.metric_ref == REVENUE_REF
    assert query.dialect == "clickhouse"
    assert query.grain_keys == ["date", "store_id"]
    assert 'SUM("revenue") AS "revenue"' in query.sql
    assert '"dm"."sales_daily"' in query.sql
    assert 'GROUP BY "date", "store_id"' in query.sql


def test_the_query_carries_no_window_of_its_own(tmp_path: Path) -> None:
    """A metric is not a chart: the time window belongs to the product, the filter to the run."""
    compiled = compile_metric_queries(snapshot_of(tmp_path, aggregate_contract()), [REVENUE_REF])

    sql = compiled.queries[0].sql
    assert "WHERE" not in sql
    assert "LIMIT" not in sql
    assert compiled.queries[0].event_time_column == "date"


def test_the_aggregate_mapping_is_the_one_the_chart_path_uses() -> None:
    """One mapping, not two: a new aggregation must not compile differently here (§8.5)."""
    assert sql_contract.aggregate_expr is sqlgen.aggregate_expr


def test_count_distinct_keeps_its_distinct(tmp_path: Path) -> None:
    contract = aggregate_contract()
    contract["formula"]["aggregate"] = {
        "table": "dm.sales_daily",
        "column": "store_id",
        "agg": "count_distinct",
    }
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [REVENUE_REF])

    assert 'COUNT(DISTINCT "store_id")' in compiled.queries[0].sql


def test_the_digest_addresses_the_sql_it_was_computed_from(tmp_path: Path) -> None:
    compiled = compile_metric_queries(snapshot_of(tmp_path, aggregate_contract()), [REVENUE_REF])

    query = compiled.queries[0]
    assert query.digest == digest_bytes(query.sql.encode("utf-8"))


def test_the_same_contract_compiles_to_the_same_bytes(tmp_path: Path) -> None:
    first = compile_metric_queries(snapshot_of(tmp_path / "a", aggregate_contract()), [REVENUE_REF])
    second = compile_metric_queries(
        snapshot_of(tmp_path / "b", aggregate_contract()), [REVENUE_REF]
    )

    assert first == second


def test_a_changed_aggregate_changes_the_query(tmp_path: Path) -> None:
    """What the check exists to show: the formula change as a query a reviewer can read."""
    changed = aggregate_contract()
    changed["formula"]["aggregate"]["agg"] = "avg"

    base = compile_metric_queries(snapshot_of(tmp_path / "a", aggregate_contract()), [REVENUE_REF])
    candidate = compile_metric_queries(snapshot_of(tmp_path / "b", changed), [REVENUE_REF])

    assert base.queries[0].digest != candidate.queries[0].digest
    assert 'AVG("revenue")' in candidate.queries[0].sql


# --- what the gate refuses to guess -------------------------------------------


def test_a_governed_ratio_is_named_not_guessed(tmp_path: Path) -> None:
    compiled = compile_metric_queries(snapshot_of(tmp_path, metric_contract()), [RATIO_REF])

    assert compiled.queries == []
    finding = compiled.findings[0]
    assert finding.code == "sql.ratio_unverified"
    assert finding.severity is Severity.ADVISORY
    assert finding.subject == RATIO_REF


def test_an_external_formula_is_not_re_derived(tmp_path: Path) -> None:
    contract = aggregate_contract()
    contract["formula"] = {
        "kind": "external_ref",
        "external": {
            "source": "dbt",
            "ref": "metric.jaffle.revenue",
            "source_revision": "9f2c1ab4d5e6f708192a3b4c5d6e7f8091a2b3c4",
        },
    }
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [REVENUE_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.opaque_formula"]


def test_reviewed_sql_stays_the_reviewers_text(tmp_path: Path) -> None:
    contract = aggregate_contract()
    contract["formula"] = {
        "kind": "reviewed_sql",
        "sql": "SELECT 1",
        "review": {
            "reviewer": "data-governance",
            "reviewed_at": "2026-08-01T10:00:00Z",
            "rationale": "legacy definition, kept verbatim",
        },
    }
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [REVENUE_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.opaque_formula"]


def test_a_term_on_another_table_needs_a_join_the_gate_will_not_invent(tmp_path: Path) -> None:
    contract = aggregate_contract()
    contract["formula"]["aggregate"] = {"table": "dm.stores", "column": "id", "agg": "count"}
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [REVENUE_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.join_required"]


def test_a_metric_this_snapshot_does_not_hold_is_reported(tmp_path: Path) -> None:
    compiled = compile_metric_queries(
        snapshot_of(tmp_path, aggregate_contract()), [REVENUE_REF, RATIO_REF]
    )

    assert [query.metric_ref for query in compiled.queries] == [REVENUE_REF]
    assert [item.code for item in compiled.findings] == ["sql.metric_absent"]
    assert compiled.findings[0].subject == RATIO_REF


# --- inside the gate ----------------------------------------------------------


def test_the_gate_carries_both_sides_reference_queries(tmp_path: Path) -> None:
    """Step 6 is no longer a blanket "not implemented": it says what it compiled and why not."""
    from auto_bi.verification.release_gate import CheckStatus
    from tests.test_decision_verify import Case, _check_payload, status_of, summary_of

    report = Case(tmp_path).with_candidate().run()

    payload = _check_payload(tmp_path, report, "sql.compile")
    assert set(payload) == {"base", "candidate", "moved"}
    assert payload["moved"] == []
    # the fixture product is governed by a ratio, which this gate refuses to guess offline
    assert [item["code"] for item in payload["candidate"]["findings"]] == ["sql.ratio_unverified"]
    assert summary_of(report, "sql.compile") == "sql.not_compiled"
    assert status_of(report, "sql.compile") is CheckStatus.ADVISORY
    assert not any(
        check.summary_code == "check.not_implemented" and check.check_id == "sql.compile"
        for check in report.checks
    )


def test_a_table_without_a_declared_engine_says_which_dialect_it_assumed(tmp_path: Path) -> None:
    model = semantic_model()
    model.tables = [
        (
            Table.model_validate({**table.model_dump(), "physical": None})
            if table.name == "dm.sales_daily"
            else table
        )
        for table in model.tables
    ]
    compiled = compile_metric_queries(
        snapshot_of(tmp_path, aggregate_contract(), model=model), [REVENUE_REF]
    )

    assert [item.code for item in compiled.findings] == ["sql.engine_undeclared"]
    assert "clickhouse" in compiled.findings[0].detail
    assert compiled.queries[0].dialect == "clickhouse"  # named as assumed, still emitted
