"""Shared fixtures: demo semantic model mirroring the docker demo-DM star."""

import pytest

from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)


@pytest.fixture
def demo_model() -> SemanticModel:
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
