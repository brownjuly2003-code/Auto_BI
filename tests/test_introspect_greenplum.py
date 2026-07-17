"""GreenplumIntrospector on a stubbed RunQuery (no live Greenplum).

Live introspection is exercised against the GP stand on the Mac; these lock the
parsing/assembly logic: role heuristics, DISTRIBUTED BY parsing, n_distinct (incl.
the negative-fraction form), partition key, and the self-reference FK skip.
"""

import re

from auto_bi.introspect.greenplum import (
    GreenplumIntrospector,
    _guess_fk,
    _parse_distribution_key,
    _role_for,
)
from auto_bi.semantic.model import Aggregation, ColumnRole

# --- pure helpers ------------------------------------------------------------


def test_role_for_heuristics() -> None:
    assert _role_for("date", "date") == (ColumnRole.TIME, None, None)
    assert _role_for("ts", "timestamp without time zone") == (ColumnRole.TIME, None, None)
    assert _role_for("store_id", "integer") == (ColumnRole.DIMENSION, None, None)
    assert _role_for("id", "bigint") == (ColumnRole.DIMENSION, None, None)
    assert _role_for("revenue", "numeric(12,2)") == (ColumnRole.MEASURE, Aggregation.SUM, None)
    assert _role_for("qty", "integer") == (ColumnRole.MEASURE, Aggregation.SUM, None)
    assert _role_for("city", "text") == (ColumnRole.DIMENSION, None, None)


def test_parse_distribution_key() -> None:
    assert _parse_distribution_key("DISTRIBUTED BY (store_id)") == ["store_id"]
    assert _parse_distribution_key("DISTRIBUTED BY (a, b)") == ["a", "b"]
    assert _parse_distribution_key("DISTRIBUTED RANDOMLY") == []
    assert _parse_distribution_key("DISTRIBUTED REPLICATED") == []
    assert _parse_distribution_key(None) == []


def test_guess_fk_skips_self_reference() -> None:
    cols = {"dm.stores": {"store_id", "city"}, "dm.sales": {"store_id", "revenue"}}
    # the fact's store_id -> the dimension
    assert _guess_fk("store_id", "dm", cols, "dm.sales") == "dm.stores.store_id"
    # the dimension's own store_id is NOT a foreign key into itself
    assert _guess_fk("store_id", "dm", cols, "dm.stores") is None
    assert _guess_fk("revenue", "dm", cols, "dm.sales") is None  # not an _id column


def test_partition_key_multi_level_ordered_by_level() -> None:
    """RANGE(date) SUBPARTITION BY LIST(region) -> 'date, region' (top level first);
    single-level -> one column; non-partitioned -> ''. Live-verified on the GP stand.

    Also locks the catalog query shape (level ordering + template-row exclusion) so an
    accidental drop of either clause is caught here, not only at the live fixture."""
    # one non-template pg_partition row per level, returned level-ordered by the SQL
    captured: dict[str, str] = {}

    def cap(sql: str) -> list[dict]:
        captured["sql"] = sql
        return [{"attname": "date"}, {"attname": "region"}]

    ml = GreenplumIntrospector(cap)._partition_key("dm", "sales_ml")
    assert ml == "date, region"
    assert "ORDER BY p.parlevel" in captured["sql"]  # all levels, top first
    assert "paristemplate = false" in captured["sql"]  # exclude SUBPARTITION TEMPLATE row

    single = GreenplumIntrospector(lambda sql: [{"attname": "date"}])._partition_key("dm", "sales")
    assert single == "date"

    assert GreenplumIntrospector(lambda sql: [])._partition_key("dm", "stores") == ""


# --- full flow on a stub -----------------------------------------------------

_TABLES = [
    {"name": "products", "comment": "Товары", "distributedby": "DISTRIBUTED BY (product_id)"},
    {"name": "sales", "comment": "Продажи", "distributedby": "DISTRIBUTED BY (store_id)"},
    {"name": "stores", "comment": None, "distributedby": "DISTRIBUTED REPLICATED"},
]
_COLUMNS = {
    "products": [
        {"name": "product_id", "type": "integer", "comment": None},
        {"name": "category", "type": "text", "comment": None},
    ],
    "sales": [
        {"name": "date", "type": "date", "comment": None},
        {"name": "store_id", "type": "integer", "comment": None},
        {"name": "product_id", "type": "integer", "comment": None},
        {"name": "revenue", "type": "numeric(12,2)", "comment": "Выручка"},
    ],
    "stores": [
        {"name": "store_id", "type": "integer", "comment": None},
        {"name": "city", "type": "text", "comment": None},
    ],
}
_NDISTINCT = {
    "sales": [
        {"attname": "store_id", "n_distinct": 20},
        {"attname": "revenue", "n_distinct": -0.3},  # negative => fraction of rows
    ],
    "products": [{"attname": "category", "n_distinct": 8}],
    "stores": [{"attname": "city", "n_distinct": 5}],
}


def _which_table(sql: str) -> str | None:
    m = re.search(
        r"relname = '(\w+)'|'dm\.(\w+)'::regclass|tablename = '(\w+)'|\"dm\".\"(\w+)\"", sql
    )
    return next((g for g in m.groups() if g), None) if m else None


def fake_run(sql: str) -> list[dict]:
    if "pg_get_table_distributedby(c.oid) AS distributedby" in sql:
        return _TABLES
    table = _which_table(sql)
    if "format_type" in sql:
        return _COLUMNS[table]
    if "p.paratts" in sql:
        return [{"attname": "date"}] if table == "sales" else []
    if "c.reltuples::bigint AS n FROM pg_class c WHERE c.oid" in sql:
        return [{"n": {"sales": 0, "stores": 20, "products": 50}[table]}]
    if "i.inhparent" in sql:  # partition children sum — only sales is partitioned
        return [{"n": 300000 if table == "sales" else 0}]
    if "pg_stats" in sql:
        return _NDISTINCT.get(table, [])
    if "GROUP BY v ORDER BY cnt" in sql:
        return [{"v": "cat_1"}, {"v": "cat_2"}] if table == "products" else [{"v": "city_1"}]
    raise AssertionError(f"unexpected query: {sql[:80]}")


def test_introspect_full_flow() -> None:
    model = GreenplumIntrospector(fake_run, schema="dm").introspect()

    assert [t.name for t in model.tables] == ["dm.products", "dm.sales", "dm.stores"]

    sales = model.table("dm.sales")
    assert sales.physical.engine == "greenplum"
    assert sales.physical.distribution_key == ["store_id"]
    assert sales.physical.partition_key == "date"
    assert sales.physical.rows == 300000  # summed from partition children
    assert sales.physical.cardinality["store_id"] == 20
    assert sales.physical.cardinality["revenue"] == 90000  # -0.3 * 300000
    assert sales.column("date").role == ColumnRole.TIME
    assert sales.column("revenue").role == ColumnRole.MEASURE

    # REPLICATED dimension -> no hash distribution key
    assert model.table("dm.stores").physical.distribution_key == []

    # only real FKs, no self-references (stores.store_id is the dim's own PK)
    assert sorted((j.left, j.right) for j in model.joins) == [
        ("dm.sales.product_id", "dm.products.product_id"),
        ("dm.sales.store_id", "dm.stores.store_id"),
    ]
    # low-cardinality dimension gets sampled top values; _id columns do not
    assert model.table("dm.products").column("category").top_values == ["cat_1", "cat_2"]
    assert model.table("dm.products").column("product_id").top_values == []
