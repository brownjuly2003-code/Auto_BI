"""Greenplum advisor pack: motion / partition-pruning / skew rules, EXPLAIN parsing,
and engine-based rule-pack selection. Live behavior is verified on the GP stand."""

from auto_bi.advisor.clickhouse import RULES as CH_RULES
from auto_bi.advisor.clickhouse import RuleContext
from auto_bi.advisor.core import Advisor
from auto_bi.advisor.greenplum import RULES as GP_RULES
from auto_bi.advisor.greenplum import (
    distribution_skew,
    gp_explain_evidence,
    non_colocated_join,
    partition_not_pruned,
)
from auto_bi.ir.spec import ChartQuery, JoinSpec, Measure, QueryFilter
from auto_bi.semantic.model import Aggregation, Column, ColumnRole, Physical, SemanticModel, Table


def _ctx(query: ChartQuery, physical: Physical, evidence: dict | None = None) -> RuleContext:
    table = Table(name=query.table, columns=[], physical=physical)
    return RuleContext(
        chart_id="c", query=query, table=table, physical=physical, evidence=evidence or {}
    )


def _phys(**kw) -> Physical:
    return Physical(engine="greenplum", **kw)


_REV = Measure(column="revenue", agg=Aggregation.SUM)


# --- EXPLAIN evidence parsing ------------------------------------------------


def test_gp_explain_evidence_parses_motion_and_partitions() -> None:
    plan = (
        "Gather Motion 2:1\n  ->  Hash Join\n"
        "    ->  Broadcast Motion 2:2\n"
        "    ->  Partition Selector\n      Partitions selected: 7 (out of 7)"
    )
    ev = gp_explain_evidence(lambda sql: [{"QUERY PLAN": plan}], "SELECT 1")
    assert ev == {"motions": ["Broadcast"], "partitions_selected": 7, "partitions_total": 7}


def test_gp_explain_evidence_never_raises() -> None:
    def boom(sql: str) -> list[dict]:
        raise RuntimeError("explain failed")

    assert gp_explain_evidence(boom, "SELECT 1") is None  # advisory-only: degrade, never raise


# --- non_colocated_join ------------------------------------------------------


def test_non_colocated_join_fires_off_distribution_key() -> None:
    q = ChartQuery(
        table="dm.sales",
        dimensions=["dm.products.category"],
        measures=[_REV],
        joins=[
            JoinSpec(
                table="dm.products",
                on_left="dm.sales.product_id",
                on_right="dm.products.product_id",
            )
        ],
    )
    findings = non_colocated_join(
        _ctx(q, _phys(distribution_key=["store_id"]), {"motions": ["Broadcast"]})
    )
    assert len(findings) == 1
    assert findings[0].rule == "non_colocated_join"
    assert "Broadcast" in findings[0].title


def test_non_colocated_join_silent_when_join_is_on_distribution_key() -> None:
    q = ChartQuery(
        table="dm.sales",
        dimensions=["dm.stores.city"],
        measures=[_REV],
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales.store_id", on_right="dm.stores.store_id")
        ],
    )
    assert non_colocated_join(_ctx(q, _phys(distribution_key=["store_id"]))) == []


# --- partition_not_pruned ----------------------------------------------------


def test_partition_not_pruned_fires_when_all_partitions_scanned() -> None:
    q = ChartQuery(
        table="dm.sales",
        dimensions=["store_id"],
        measures=[_REV],
        filters=[QueryFilter(column="store_id", op="=", value=5)],
    )
    ev = {"partitions_selected": 7, "partitions_total": 7}
    findings = partition_not_pruned(_ctx(q, _phys(partition_key="date"), ev))
    assert len(findings) == 1 and findings[0].rule == "partition_not_pruned"


def test_partition_not_pruned_silent_when_pruned_or_unfiltered() -> None:
    q_filtered = ChartQuery(
        table="dm.sales",
        dimensions=["store_id"],
        measures=[_REV],
        filters=[QueryFilter(column="date", op=">=", value="2026-03-01")],
    )
    # pruned: fewer partitions than total
    assert (
        partition_not_pruned(
            _ctx(
                q_filtered,
                _phys(partition_key="date"),
                {"partitions_selected": 2, "partitions_total": 7},
            )
        )
        == []
    )
    # no filters at all -> rule doesn't apply (a different rule covers full scans)
    q_unfiltered = ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[_REV])
    assert (
        partition_not_pruned(
            _ctx(
                q_unfiltered,
                _phys(partition_key="date"),
                {"partitions_selected": 7, "partitions_total": 7},
            )
        )
        == []
    )


# --- distribution_skew -------------------------------------------------------


def test_distribution_skew_fires_on_low_cardinality_key_large_fact() -> None:
    q = ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[_REV])
    phys = _phys(distribution_key=["store_id"], rows=20_000_000, cardinality={"store_id": 20})
    findings = distribution_skew(_ctx(q, phys))
    assert len(findings) == 1 and findings[0].rule == "distribution_skew"


def test_distribution_skew_remediation_picks_higher_cardinality_key() -> None:
    q = ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[_REV])
    phys = _phys(
        distribution_key=["store_id"],
        rows=20_000_000,
        cardinality={"store_id": 20, "order_id": 5_000_000},
    )
    (f,) = distribution_skew(_ctx(q, phys))
    assert f.remediation is not None and f.remediation.kind == "gp_redistribute"
    assert f.remediation.ddl == "ALTER TABLE dm.sales SET DISTRIBUTED BY (order_id);"


def test_distribution_skew_remediation_falls_back_to_random() -> None:
    # no other column with cardinality >= the even-spread threshold -> random distribution
    q = ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[_REV])
    phys = _phys(
        distribution_key=["store_id"], rows=20_000_000, cardinality={"store_id": 20, "region": 8}
    )
    (f,) = distribution_skew(_ctx(q, phys))
    assert f.remediation is not None
    assert f.remediation.ddl == "ALTER TABLE dm.sales SET DISTRIBUTED RANDOMLY;"


def test_distribution_skew_silent_on_high_cardinality_or_small_fact() -> None:
    q = ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[_REV])
    # high-cardinality key spreads evenly
    assert (
        distribution_skew(
            _ctx(
                q,
                _phys(
                    distribution_key=["store_id"], rows=20_000_000, cardinality={"store_id": 50_000}
                ),
            )
        )
        == []
    )
    # small fact: skew doesn't matter
    assert (
        distribution_skew(
            _ctx(
                q, _phys(distribution_key=["store_id"], rows=300_000, cardinality={"store_id": 20})
            )
        )
        == []
    )


# --- engine-based rule-pack selection ----------------------------------------


def _model(engine: str) -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales",
                columns=[Column(name="revenue", type="numeric", role=ColumnRole.MEASURE)],
                physical=Physical(engine=engine),
            )
        ]
    )


def test_advisor_selects_pack_by_engine() -> None:
    assert Advisor(_model("greenplum"))._rules is GP_RULES
    assert Advisor(_model("clickhouse"))._rules is CH_RULES
    assert Advisor(_model("greenplum"))._dialect == "postgres"


# --- GP eval suite (deterministic, Phase 3.5) --------------------------------


def test_gp_advisor_suite_passes_on_committed_gp_model() -> None:
    """The GP anti-pattern cases fire (and clean cases stay silent) against the real
    committed demo model_gp.yaml — locks the GP rule pack in the user-facing eval gate.
    At-scale cases seed rows>=10M themselves; the model ships at 300k."""
    from pathlib import Path

    from auto_bi.eval.cases import (
        GP_ADVISOR_CASES,
        advisor_cases_for_engine,
        golden_cases_for_engine,
    )
    from auto_bi.eval.runner import run_advisor_suite
    from auto_bi.semantic.model import SemanticModel

    model = SemanticModel.load(str(Path(__file__).parents[1] / "semantic" / "model_gp.yaml"))
    report = run_advisor_suite(model, GP_ADVISOR_CASES)
    failed = [(r.case_id, r.detail) for r in report.results if not r.passed]
    assert not failed, failed
    assert report.total == len(GP_ADVISOR_CASES)

    # engine dispatch picks the GP set for a GP model, CH set otherwise
    assert advisor_cases_for_engine("greenplum") is GP_ADVISOR_CASES
    assert len(golden_cases_for_engine("greenplum")) == 14  # GP golden authored in Phase 3.5
    assert advisor_cases_for_engine("clickhouse") is not GP_ADVISOR_CASES
