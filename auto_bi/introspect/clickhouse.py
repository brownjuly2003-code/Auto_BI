"""ClickHouse introspector (reference implementation, v1).

Reads system.tables / system.columns plus light profiling queries and produces a draft
SemanticModel: roles by heuristics, physical layer (sorting/partition keys, sizes,
cardinalities), top values of low-cardinality dimensions for grounding.
The draft is meant to be hand-edited and committed as semantic/model.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from auto_bi.config import Settings
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)

# run_query(sql) -> rows as dicts; the only seam to the real client (stubbed in tests)
RunQuery = Callable[[str], list[dict]]

_IDENT_RE = re.compile(r"^\w+$")
_NUMERIC_PREFIXES = ("Int", "UInt", "Float", "Decimal")
_TIME_PREFIXES = ("Date", "DateTime", "DateTime64", "Date32")

SAMPLE_LIMIT = 1_000_000  # profile big facts on a LIMIT-ed subquery, not a full scan
TOP_VALUES_MAX_CARDINALITY = 50
TOP_VALUES_LIMIT = 20


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return f"`{name}`"


def _unwrap_type(ch_type: str) -> str:
    """Nullable(LowCardinality(String)) -> String."""
    inner = ch_type
    for wrapper in ("Nullable", "LowCardinality"):
        inner = re.sub(rf"^{wrapper}\((.*)\)$", r"\1", inner)
    return inner


def _role_for(name: str, ch_type: str) -> tuple[ColumnRole, Aggregation | None]:
    base = _unwrap_type(ch_type)
    if base.startswith(_TIME_PREFIXES):
        return ColumnRole.TIME, None
    if name == "id" or name.endswith("_id"):
        return ColumnRole.DIMENSION, None
    if base.startswith(_NUMERIC_PREFIXES):
        return ColumnRole.MEASURE, Aggregation.SUM
    return ColumnRole.DIMENSION, None


def _guess_fk(column_name: str, database: str, table_names: set[str]) -> str | None:
    """store_id -> dm.stores.id when such a table exists."""
    if not column_name.endswith("_id"):
        return None
    stem = column_name.removesuffix("_id")
    for candidate in (f"{stem}s", f"{stem}es", stem):
        if f"{database}.{candidate}" in table_names:
            return f"{database}.{candidate}.id"
    return None


class ClickHouseIntrospector:
    def __init__(self, run_query: RunQuery, database: str | None = None) -> None:
        self._run = run_query
        self._default_db = database

    def introspect(self, database: str | None = None) -> SemanticModel:
        db = database or self._default_db
        if not db:
            raise ValueError("database is required")
        _ident(db)  # validate before interpolation

        tables_meta = self._run(
            "SELECT name, engine, sorting_key, partition_key, total_rows, total_bytes, comment "
            f"FROM system.tables WHERE database = '{db}' AND engine LIKE '%MergeTree%' "
            "ORDER BY name"
        )
        columns_meta = self._run(
            "SELECT table, name, type, comment FROM system.columns "
            f"WHERE database = '{db}' ORDER BY table, position"
        )

        full_names = {f"{db}.{t['name']}" for t in tables_meta}
        tables: list[Table] = []
        joins: list[Join] = []

        for t in tables_meta:
            full_name = f"{db}.{t['name']}"
            columns = []
            for c in (c for c in columns_meta if c["table"] == t["name"]):
                role, agg = _role_for(c["name"], c["type"])
                fk = _guess_fk(c["name"], db, full_names) if role == ColumnRole.DIMENSION else None
                if fk:
                    joins.append(Join(left=f"{full_name}.{c['name']}", right=fk))
                columns.append(
                    Column(
                        name=c["name"],
                        type=c["type"],
                        role=role,
                        agg=agg,
                        fk=fk,
                        description=c["comment"] or "",
                    )
                )

            sorting_key = [k.strip() for k in (t["sorting_key"] or "").split(",") if k.strip()]
            rows = int(t["total_rows"] or 0)
            cardinality = self._profile_cardinality(db, t["name"], columns, rows)
            self._fill_top_values(db, t["name"], columns, cardinality, rows)

            tables.append(
                Table(
                    name=full_name,
                    description=t["comment"] or "",
                    grain=sorting_key,  # draft: sorting key as grain, hand-corrected later
                    columns=columns,
                    physical=Physical(
                        engine="clickhouse",
                        table_engine=t["engine"],
                        sorting_key=sorting_key,
                        partition_key=t["partition_key"] or "",
                        rows=rows,
                        bytes=int(t["total_bytes"] or 0),
                        cardinality=cardinality,
                    ),
                )
            )

        return SemanticModel(tables=tables, joins=joins)

    def _source(self, db: str, table: str, rows: int) -> str:
        """FROM clause: big tables are profiled on a LIMIT-ed subquery."""
        target = f"{_ident(db)}.{_ident(table)}"
        if rows > SAMPLE_LIMIT:
            return f"(SELECT * FROM {target} LIMIT {SAMPLE_LIMIT})"
        return target

    def _profile_cardinality(
        self, db: str, table: str, columns: list[Column], rows: int
    ) -> dict[str, int]:
        dims = [c.name for c in columns if c.role == ColumnRole.DIMENSION]
        if not dims or rows == 0:
            return {}
        exprs = ", ".join(f"uniqCombined({_ident(d)}) AS {_ident(d)}" for d in dims)
        result = self._run(f"SELECT {exprs} FROM {self._source(db, table, rows)}")
        return {name: int(value) for name, value in result[0].items()} if result else {}

    def _fill_top_values(
        self, db: str, table: str, columns: list[Column], cardinality: dict[str, int], rows: int
    ) -> None:
        for col in columns:
            if col.role != ColumnRole.DIMENSION or col.name.endswith("_id") or col.name == "id":
                continue
            uniq = cardinality.get(col.name, TOP_VALUES_MAX_CARDINALITY + 1)
            if uniq > TOP_VALUES_MAX_CARDINALITY:
                continue
            result = self._run(
                f"SELECT toString({_ident(col.name)}) AS v, count() AS cnt "
                f"FROM {self._source(db, table, rows)} "
                f"GROUP BY v ORDER BY cnt DESC LIMIT {TOP_VALUES_LIMIT}"
            )
            col.top_values = [r["v"] for r in result]


def make_run_query(settings: Settings) -> RunQuery:
    """Real clickhouse-connect client behind the RunQuery seam (read-only role)."""
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=settings.ch_host,
        port=settings.ch_port,
        username=settings.ch_user,
        password=settings.ch_password,
    )

    def run(sql: str) -> list[dict]:
        return list(client.query(sql).named_results())

    return run
