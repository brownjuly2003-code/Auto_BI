"""Subprocess helper for offline browser E2E (plan_sol step 10).

Run from repo root (cwd set by the test). Env:

  AUTO_BI_E2E_PORT       — bind port
  AUTO_BI_E2E_STORE      — sqlite path
  AUTO_BI_E2E_FAIL_TIMES — how many builder calls fail before success (default 0)
"""

from __future__ import annotations

import os

import uvicorn

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.ir.spec import TargetBI
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.store import Store
from tests.test_machine import CLEAR_REPORT, ScriptedLLM
from tests.test_propose import GOOD_SPEC


def _demo_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                description="Дневные продажи",
                grain=["date", "store_id", "product_id"],
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(
                        name="store_id",
                        type="UInt32",
                        role=ColumnRole.DIMENSION,
                        fk="dm.stores.id",
                    ),
                    Column(name="product_id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                        description="Выручка, руб",
                    ),
                    Column(
                        name="orders",
                        type="UInt32",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(
                    engine="clickhouse",
                    table_engine="MergeTree",
                    sorting_key=["date", "store_id", "product_id"],
                    partition_key="toYYYYMM(date)",
                    rows=100_000_000,
                ),
            ),
            Table(
                name="dm.stores",
                description="Справочник магазинов",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="name", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="city",
                        type="LowCardinality(String)",
                        role=ColumnRole.DIMENSION,
                        top_values=["Москва", "Казань"],
                    ),
                ],
                physical=Physical(engine="clickhouse", table_engine="MergeTree", rows=4200),
            ),
        ],
        joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
    )


class _Builder:
    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self._n = 0

    def __call__(self, spec, log, session_id):
        self._n += 1
        if self._n <= self._fail_times:
            log("BI unavailable")
            raise RuntimeError("BI down (injected for E2E retry)")
        log("SQL ok")
        log("BUILD done")
        return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")


def main() -> None:
    port = int(os.environ["AUTO_BI_E2E_PORT"])
    store_path = os.environ["AUTO_BI_E2E_STORE"]
    fail_times = int(os.environ.get("AUTO_BI_E2E_FAIL_TIMES", "0"))
    store = Store(store_path)
    builder = _Builder(fail_times)
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, GOOD_SPEC, GOOD_SPEC, GOOD_SPEC])
    app = create_app(
        model=_demo_model(),
        llm=llm,
        store=store,
        builder=builder,
        demo_auto_only=False,
        bi_base_urls={TargetBI.SUPERSET: "http://bi.example:8088"},
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
