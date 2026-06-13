"""Live validation of the at-scale GP advisor rules (Phase 3.3/3.4).

The 300k demo can't trigger the two rules gated on physical.rows >= 10M:
  * no_filter_on_large_fact (CRITICAL)
  * distribution_skew       (WARN, DM_CHANGE_REQUEST)
After scripts/stand_scale_gp_dm.sql tops dm.sales past 10M, this script introspects
the live GP stand (real reltuples + pg_stats cardinality), then runs the GP advisor
on two charts to show both rules behaving correctly at scale:
  chart A (no filter)   -> distribution_skew + no_filter_on_large_fact
  chart B (date filter) -> distribution_skew only (the filter clears no_filter)

Run via the SSH tunnel to the GP stand (see runbook):
  AUTO_BI_GP_HOST=127.0.0.1 AUTO_BI_GP_PORT=15433 \
  AUTO_BI_GP_USER=auto_bi_ro AUTO_BI_GP_PASSWORD=ro_pw \
    uv run python scripts/gp_scale_validate.py
"""

from __future__ import annotations

import os

from auto_bi.advisor.core import Advisor
from auto_bi.config import get_settings
from auto_bi.introspect.greenplum import GreenplumIntrospector, make_run_query_pg
from auto_bi.ir.spec import ChartQuery, ChartSpec, FilterOp, Measure, QueryFilter, Viz
from auto_bi.semantic.model import Aggregation

settings = get_settings()
run_query = make_run_query_pg(settings)

model = GreenplumIntrospector(run_query, schema=settings.gp_schema).introspect()
# dump to a scratch path: the committed semantic/model_gp.yaml stays the canonical
# 300k demo (fast rebuilds); this validation re-introspects the scaled stand transiently
os.makedirs(".tmp", exist_ok=True)
model.dump(".tmp/model_gp_scaled.yaml")

sales = model.table("dm.sales")
phys = sales.physical
print("=== introspected dm.sales physical (live GP) ===")
print(f"rows             : {phys.rows}")
print(f"distribution_key : {phys.distribution_key}")
print(f"partition_key    : {phys.partition_key!r}")
print(f"store_id n_distinct: {phys.cardinality.get('store_id')}")
print(f">= 10M threshold : {phys.rows >= 10_000_000}")

advisor = Advisor(model, run_query=run_query)
rev = Measure(column="revenue", agg=Aggregation.SUM)


def show(title: str, chart: ChartSpec) -> None:
    findings = advisor.review_chart(chart)
    print(f"\n=== {title} ===")
    print(f"SQL filters: {[f.column for f in chart.query.filters] or 'NONE'}")
    for f in findings:
        print(f"  [{f.severity.value:8}] {f.rule:24} ({f.verdict_class.value}) {f.title}")
    if not findings:
        print("  (no findings)")


show(
    "chart A — revenue by store, NO filter",
    ChartSpec(
        id="A",
        title="Выручка по магазинам",
        viz=Viz.TABLE,
        query=ChartQuery(table="dm.sales", dimensions=["store_id"], measures=[rev]),
    ),
)

show(
    "chart B — revenue by store, date filter (>= 2026-04-01)",
    ChartSpec(
        id="B",
        title="Выручка по магазинам с апреля",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.sales",
            dimensions=["store_id"],
            measures=[rev],
            filters=[QueryFilter(column="date", op=FilterOp.GTE, value="2026-04-01")],
        ),
    ),
)
