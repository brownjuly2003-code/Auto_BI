"""Reference SQL for a governed metric (ARCHITECTURE §8.2 step 6).

Step 6 compiles the query a metric *means* — its definition at its own declared grain, not
a chart's windowed, filtered, top-N view of it. Two properties make that worth having in
evidence: a reviewer reading "formula changed" can see the before and after as SQL, and
step 7 later has something concrete to execute.

The boundary is the interesting part. This compiler emits a query only where it can vouch
for the semantics offline:

* a typed aggregate at its grain is exact in both engines — it compiles;
* a governed ratio compiles on ClickHouse only, and only through `divideDecimal` at the
  precision the contract declares. Plain `Decimal / Decimal` keeps the *dividend's* scale
  there (a margin over a Decimal(18,2) numerator truncates to two digits), and the chart
  path answers that by casting to Float64 — a display decision a governed decimal metric
  must not inherit. The zero guard is the engine's, not a matter of taste: `divideDecimal`
  reads the denominator's values under the NULL mask, so the usual `NULLIF(den, 0)` still
  raises and the query has to keep the zero out of the call. On any other dialect the form
  is unverified and the gate keeps naming the gap;
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


def ratio_contract(**terms: Any) -> dict[str, Any]:
    """The fixture ratio, with numerator/denominator/precision overridden per test."""
    contract = metric_contract()
    precision = terms.pop("precision", None)
    contract["formula"].update(terms)
    if precision is not None:
        contract["unit"]["precision"] = precision
    return contract


def model_with(*columns: dict[str, Any]) -> SemanticModel:
    """The fixture model with extra columns on the fact table."""
    model = semantic_model()
    fact = model.tables[0].model_dump()
    fact["columns"] = [*fact["columns"], *columns]
    model.tables = [Table.model_validate(fact), *model.tables[1:]]
    return model


def clickhouse_off(model: SemanticModel) -> SemanticModel:
    """The same fact table on a Greenplum/Greengage mart — the v2 engine."""
    fact = model.tables[0].model_dump()
    fact["physical"]["engine"] = "greenplum"
    model.tables = [Table.model_validate(fact), *model.tables[1:]]
    return model


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


# --- a governed ratio ---------------------------------------------------------


def test_a_governed_ratio_compiles_at_the_precision_it_declares(tmp_path: Path) -> None:
    """`divideDecimal` divides exactly and truncates at the scale it is told to produce —
    whatever scales the terms carry. Verified on the pinned stand (ClickHouse 24.8.14.39)."""
    compiled = compile_metric_queries(snapshot_of(tmp_path, metric_contract()), [RATIO_REF])

    assert compiled.findings == []
    query = compiled.queries[0]
    assert query.metric_ref == RATIO_REF
    assert query.grain_keys == ["date", "store_id"]
    assert 'divideDecimal(SUM("gross_margin"), COALESCE(nullIf(SUM("revenue"), 0), 1), 6)' in (
        query.sql
    )
    assert 'AS "gross_margin_pct"' in query.sql
    assert 'GROUP BY "date", "store_id"' in query.sql


def test_the_declared_precision_is_part_of_the_query(tmp_path: Path) -> None:
    """Precision is where the number stops being exact, so it belongs to the metric's
    meaning: coarsen it and the reference query moves for a reviewer to see."""
    base = compile_metric_queries(snapshot_of(tmp_path / "a", metric_contract()), [RATIO_REF])
    coarse = compile_metric_queries(
        snapshot_of(tmp_path / "b", ratio_contract(precision=2)), [RATIO_REF]
    )

    assert base.queries[0].digest != coarse.queries[0].digest
    assert 'COALESCE(nullIf(SUM("revenue"), 0), 1), 2)' in coarse.queries[0].sql


def test_a_zero_or_missing_denominator_never_reaches_the_division(tmp_path: Path) -> None:
    """`divideDecimal` reads the denominator's values under the NULL mask: `NULLIF(den, 0)`
    alone still raises ILLEGAL_DIVISION, so the zero is replaced before the call and the
    row is nulled by the guard around it."""
    compiled = compile_metric_queries(snapshot_of(tmp_path, metric_contract()), [RATIO_REF])

    sql = compiled.queries[0].sql
    assert 'CASE WHEN COALESCE(SUM("revenue"), 0) = 0 THEN NULL' in sql
    assert 'COALESCE(nullIf(SUM("revenue"), 0), 1)' in sql


def test_the_ratio_does_not_inherit_the_chart_paths_float_cast(tmp_path: Path) -> None:
    """The Float64 cast in `sqlgen` is a display decision for dashboards; a governed decimal
    metric with a declared precision must not be computed through it."""
    compiled = compile_metric_queries(snapshot_of(tmp_path, metric_contract()), [RATIO_REF])

    sql = compiled.queries[0].sql
    assert "Float64" not in sql
    assert " / " not in sql  # no plain decimal division either: it keeps the dividend's scale


def test_integer_terms_are_cast_because_divide_decimal_takes_decimals_only(
    tmp_path: Path,
) -> None:
    contract = ratio_contract(
        numerator={"table": "dm.sales_daily", "column": "store_id", "agg": "count_distinct"},
        denominator={"table": "dm.sales_daily", "column": "store_id", "agg": "count"},
    )
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [RATIO_REF])

    sql = compiled.queries[0].sql
    assert 'CAST(COUNT(DISTINCT "store_id") AS Decimal(38, 0))' in sql
    assert 'CAST(COALESCE(nullIf(COUNT("store_id"), 0), 1) AS Decimal(38, 0))' in sql


def test_a_nullable_integer_numerator_casts_to_a_nullable_decimal(tmp_path: Path) -> None:
    """A group of nothing but NULLs sums to NULL, and casting that to a non-nullable Decimal
    is an error in ClickHouse — the metric is missing there, not zero."""
    contract = ratio_contract(
        numerator={"table": "dm.sales_daily", "column": "refunds", "agg": "sum"}
    )
    model = model_with(
        {"name": "refunds", "type": "Nullable(UInt32)", "role": "measure", "agg": "sum"}
    )
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract, model=model), [RATIO_REF])

    assert compiled.findings == []
    assert 'CAST(SUM("refunds") AS Nullable(Decimal(38, 0)))' in compiled.queries[0].sql


def test_an_averaged_term_is_named_not_compiled(tmp_path: Path) -> None:
    """AVG returns Float64: exact for a chart, not exact enough to define a governed decimal."""
    contract = ratio_contract(
        numerator={"table": "dm.sales_daily", "column": "gross_margin", "agg": "avg"}
    )
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [RATIO_REF])

    assert compiled.queries == []
    finding = compiled.findings[0]
    assert finding.code == "sql.ratio_inexact_term"
    assert finding.severity is Severity.ADVISORY
    assert "numerator" in finding.detail


def test_a_float_column_is_named_not_compiled(tmp_path: Path) -> None:
    """A sum of floats depends on the order the parts arrive in — it cannot address a
    governed number (invariant 6)."""
    contract = ratio_contract(
        denominator={"table": "dm.sales_daily", "column": "weight", "agg": "sum"}
    )
    model = model_with({"name": "weight", "type": "Float64", "role": "measure", "agg": "sum"})
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract, model=model), [RATIO_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.ratio_inexact_term"]
    assert "denominator" in compiled.findings[0].detail


def test_a_ratio_on_a_greenplum_mart_stays_a_named_gap(tmp_path: Path) -> None:
    """Only ClickHouse's decimal division has been verified on a stand; the Postgres family
    rounds and scales its `numeric` division by its own rules, so the gate says so."""
    compiled = compile_metric_queries(
        snapshot_of(tmp_path, metric_contract(), model=clickhouse_off(semantic_model())),
        [RATIO_REF],
    )

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.ratio_unverified"]
    assert "postgres" in compiled.findings[0].detail


def test_a_term_column_the_model_does_not_declare_is_named(tmp_path: Path) -> None:
    """Without the column's declared type the gate cannot tell an exact term from a float one."""
    contract = ratio_contract(
        numerator={"table": "dm.sales_daily", "column": "returns", "agg": "sum"}
    )
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [RATIO_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.term_undeclared"]
    assert "returns" in compiled.findings[0].detail


def test_a_ratio_term_on_another_table_needs_a_join_the_gate_will_not_invent(
    tmp_path: Path,
) -> None:
    contract = ratio_contract(
        denominator={"table": "dm.stores", "column": "id", "agg": "count"},
    )
    compiled = compile_metric_queries(snapshot_of(tmp_path, contract), [RATIO_REF])

    assert compiled.queries == []
    assert [item.code for item in compiled.findings] == ["sql.join_required"]


# --- what the gate refuses to guess -------------------------------------------


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
    # the fixture product is governed by a ratio, and on a ClickHouse mart that now compiles
    assert payload["candidate"]["findings"] == []
    assert [item["metric_ref"] for item in payload["candidate"]["queries"]] == [
        "metric://sales/gross_margin_pct@3"
    ]
    assert summary_of(report, "sql.compile") == "sql.compiled"
    assert status_of(report, "sql.compile") is CheckStatus.PASS
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
