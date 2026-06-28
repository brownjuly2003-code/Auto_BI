"""ClickHouseIntrospector unit tests on a stubbed RunQuery (no live ClickHouse)."""

from auto_bi.introspect.clickhouse import ClickHouseIntrospector
from auto_bi.semantic.model import Aggregation, ColumnRole, SemanticModel

TABLES = [
    {
        "name": "sales_daily",
        "engine": "MergeTree",
        "sorting_key": "date, store_id, product_id",
        "partition_key": "toYYYYMM(date)",
        "total_rows": 100_000_000,
        "total_bytes": 4_000_000_000,
        "comment": "Дневные продажи",
    },
    {
        "name": "stores",
        "engine": "MergeTree",
        "sorting_key": "id",
        "partition_key": "",
        "total_rows": 4200,
        "total_bytes": 100_000,
        "comment": "Справочник магазинов",
    },
]

COLUMNS = [
    {"table": "sales_daily", "name": "date", "type": "Date", "comment": "День продажи"},
    {"table": "sales_daily", "name": "store_id", "type": "UInt32", "comment": ""},
    {"table": "sales_daily", "name": "product_id", "type": "UInt32", "comment": ""},
    {"table": "sales_daily", "name": "revenue", "type": "Decimal(18, 2)", "comment": "Выручка"},
    {"table": "stores", "name": "id", "type": "UInt32", "comment": "ID магазина"},
    {"table": "stores", "name": "city", "type": "LowCardinality(String)", "comment": "Город"},
]


def fake_run_query(sql: str) -> list[dict]:
    if "system.tables" in sql:
        return TABLES
    if "system.columns" in sql:
        return COLUMNS
    if "uniqCombined" in sql and "sales_daily" in sql:
        if "store_id" in sql:  # sampled dimension profile
            return [{"store_id": 4200, "product_id": 2000}]
        return [{"date": 730}]  # full-table TIME-column profile
    if "uniqCombined" in sql and "stores" in sql:
        return [{"id": 4200, "city": 20}]
    if "GROUP BY" in sql and "city" in sql:
        return [{"v": "Москва", "cnt": 900}, {"v": "Казань", "cnt": 300}]
    raise AssertionError(f"unexpected query: {sql}")


def make_model() -> SemanticModel:
    return ClickHouseIntrospector(fake_run_query).introspect("dm")


def test_tables_and_physical() -> None:
    model = make_model()
    assert [t.name for t in model.tables] == ["dm.sales_daily", "dm.stores"]

    fact = model.table("dm.sales_daily")
    assert fact.physical.sorting_key == ["date", "store_id", "product_id"]
    assert fact.physical.partition_key == "toYYYYMM(date)"
    assert fact.physical.rows == 100_000_000
    assert fact.physical.cardinality == {"store_id": 4200, "product_id": 2000, "date": 730}
    assert fact.grain == ["date", "store_id", "product_id"]
    assert fact.description == "Дневные продажи"


def test_role_heuristics_and_fk() -> None:
    fact = make_model().table("dm.sales_daily")
    assert fact.column("date").role == ColumnRole.TIME
    assert fact.column("revenue").role == ColumnRole.MEASURE
    assert fact.column("revenue").agg == Aggregation.SUM
    store_id = fact.column("store_id")
    assert store_id.role == ColumnRole.DIMENSION
    assert store_id.fk == "dm.stores.id"
    # product_id has no dm.products table in the stub -> no fk
    assert fact.column("product_id").fk is None


def test_joins_from_fk() -> None:
    model = make_model()
    assert any(
        j.left == "dm.sales_daily.store_id" and j.right == "dm.stores.id" for j in model.joins
    )


def test_top_values_only_for_low_cardinality_non_id() -> None:
    stores = make_model().table("dm.stores")
    assert stores.column("city").top_values == ["Москва", "Казань"]
    assert stores.column("id").top_values == []


def test_yaml_roundtrip(tmp_path) -> None:
    model = make_model()
    path = tmp_path / "model.yaml"
    model.dump(path)
    loaded = SemanticModel.load(path)
    assert loaded == model


def test_cardinality_profile_tolerates_null_uniq() -> None:
    """uniqCombined returns NULL on Nullable(Nothing) columns (NULL literal in a CTAS)."""

    def run(sql: str) -> list[dict]:
        if "system.tables" in sql:
            return [
                {
                    "name": "degenerate",
                    "engine": "MergeTree",
                    "sorting_key": "id",
                    "partition_key": "",
                    "total_rows": 10,
                    "total_bytes": 100,
                    "comment": "",
                }
            ]
        if "system.columns" in sql:
            return [
                {"table": "degenerate", "name": "id", "type": "UInt32", "comment": ""},
                {
                    "table": "degenerate",
                    "name": "pii_source",
                    "type": "Nullable(Nothing)",
                    "comment": "",
                },
            ]
        if "uniqCombined" in sql:
            return [{"id": 10, "pii_source": None}]
        if "GROUP BY" in sql:
            return [{"v": "x", "cnt": 1}]
        raise AssertionError(f"unexpected query: {sql}")

    model = ClickHouseIntrospector(run).introspect("dm")
    table = model.table("dm.degenerate")
    assert table is not None
    assert table.physical.cardinality["pii_source"] == 0


def test_time_column_profiled_on_full_table_not_sample() -> None:
    """A fact sorted by time would undercount distinct dates from a leading-rows sample, so the
    TIME column is profiled on the full table (no LIMIT), unlike the LIMIT-ed dimension profile."""
    seen: list[str] = []

    def run(sql: str) -> list[dict]:
        seen.append(sql)
        return fake_run_query(sql)

    fact = ClickHouseIntrospector(run).introspect("dm").table("dm.sales_daily")
    assert fact is not None
    assert fact.physical.cardinality["date"] == 730  # full count, not the ~first-days sample
    date_q = next(
        s for s in seen if "uniqCombined" in s and "sales_daily" in s and "store_id" not in s
    )
    dim_q = next(s for s in seen if "uniqCombined" in s and "store_id" in s)
    assert "LIMIT" not in date_q  # time column: full scan
    assert "LIMIT" in dim_q  # dimensions: sampled (100M rows > SAMPLE_LIMIT)
