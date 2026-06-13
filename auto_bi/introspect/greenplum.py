"""Greenplum / Greengage introspector (v2 engine, Phase 3.3).

Reads PostgreSQL catalogs plus Greenplum extensions (`pg_get_table_distributedby`,
`pg_partition`) and produces a draft SemanticModel with the GP physical layer:
distribution key (advisor fuel for motion/skew rules) and the range-partition column.
Greengage is a Greenplum fork — same catalogs, same code path.

Mirrors introspect/clickhouse.py: same RunQuery seam (Callable[[str], list[dict]],
stubbed in tests) and the same draft-then-hand-edit contract for semantic/model.yaml.
"""

from __future__ import annotations

import re

from auto_bi.config import Settings
from auto_bi.engine import GREENPLUM
from auto_bi.introspect.base import RunQuery
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# PostgreSQL numeric type names that make a column a measure candidate
_NUMERIC_TYPES = ("smallint", "integer", "bigint", "numeric", "decimal", "real", "double")
_TIME_TYPES = ("date", "timestamp", "time")
_DISTKEY_RE = re.compile(r"DISTRIBUTED BY \((.*)\)", re.IGNORECASE)
LOW_CARDINALITY_MAX = 50


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _role_for(name: str, pg_type: str) -> tuple[ColumnRole, Aggregation | None]:
    base = pg_type.lower()
    if base.startswith(_TIME_TYPES):
        return ColumnRole.TIME, None
    if name == "id" or name.endswith("_id"):
        return ColumnRole.DIMENSION, None
    if base.startswith(_NUMERIC_TYPES):
        return ColumnRole.MEASURE, Aggregation.SUM
    return ColumnRole.DIMENSION, None


def _parse_distribution_key(distributedby: str | None) -> list[str]:
    """'DISTRIBUTED BY (store_id, dt)' -> ['store_id', 'dt']; RANDOMLY/REPLICATED -> []."""
    if not distributedby:
        return []
    m = _DISTKEY_RE.search(distributedby)
    if not m:
        return []  # DISTRIBUTED RANDOMLY / REPLICATED — no hash distribution key
    return [c.strip().strip('"') for c in m.group(1).split(",") if c.strip()]


def _guess_fk(
    column_name: str, schema: str, columns_by_table: dict[str, set[str]], owner_table: str
) -> str | None:
    """store_id -> dm.stores.store_id (or .id) when such a dimension table exists.

    Skips self-references: a dimension's own PK (e.g. stores.store_id) is not a foreign key.
    """
    if not column_name.endswith("_id"):
        return None
    stem = column_name.removesuffix("_id")
    for candidate in (f"{stem}s", f"{stem}es", stem):
        full = f"{schema}.{candidate}"
        if full == owner_table:  # the dimension's own key, not an FK into another table
            continue
        cols = columns_by_table.get(full)
        if cols is None:
            continue
        for key in (column_name, "id"):  # dim PK is often the same name, else "id"
            if key in cols:
                return f"{full}.{key}"
    return None


class GreenplumIntrospector:
    def __init__(self, run_query: RunQuery, schema: str | None = None) -> None:
        self._run = run_query
        self._default_schema = schema

    def introspect(self, schema: str | None = None) -> SemanticModel:
        sch = schema or self._default_schema
        if not sch:
            raise ValueError("schema is required")
        _ident(sch)

        # root tables only: partition children appear as pg_inherits.inhrelid -> exclude them
        tables_meta = self._run(
            "SELECT c.relname AS name, obj_description(c.oid) AS comment, "
            "pg_get_table_distributedby(c.oid) AS distributedby "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = '{sch}' AND c.relkind = 'r' "
            "AND c.oid NOT IN (SELECT inhrelid FROM pg_inherits) ORDER BY c.relname"
        )

        columns_by_name = {f"{sch}.{t['name']}": self._columns(sch, t["name"]) for t in tables_meta}
        columns_by_table = {
            name: {c["name"] for c in cols} for name, cols in columns_by_name.items()
        }

        tables: list[Table] = []
        joins: list[Join] = []
        for t in tables_meta:
            full_name = f"{sch}.{t['name']}"
            cardinality = self._cardinality(sch, t["name"])
            columns: list[Column] = []
            for c in columns_by_name[full_name]:
                role, agg = _role_for(c["name"], c["type"])
                fk = (
                    _guess_fk(c["name"], sch, columns_by_table, full_name)
                    if role == ColumnRole.DIMENSION
                    else None
                )
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
                        top_values=self._top_values(sch, t["name"], c["name"], role, cardinality),
                    )
                )

            tables.append(
                Table(
                    name=full_name,
                    description=t["comment"] or "",
                    columns=columns,
                    physical=Physical(
                        engine=GREENPLUM,
                        distribution_key=_parse_distribution_key(t["distributedby"]),
                        partition_key=self._partition_key(sch, t["name"]),
                        rows=self._rows(sch, t["name"]),
                        cardinality=cardinality,
                    ),
                )
            )

        return SemanticModel(tables=tables, joins=joins)

    def _columns(self, schema: str, table: str) -> list[dict]:
        return self._run(
            "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, "
            "col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = '{schema}' AND c.relname = '{_ident(table)}' "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
        )

    def _partition_key(self, schema: str, table: str) -> str:
        """Range/list partition column(s) of the root table, comma-joined ('' if none)."""
        rows = self._run(
            "SELECT a.attname FROM pg_attribute a "
            f"WHERE a.attrelid = '{schema}.{_ident(table)}'::regclass AND a.attnum IN ("
            "SELECT unnest(paratts::int2[]) FROM pg_partition "
            f"WHERE parrelid = '{schema}.{_ident(table)}'::regclass "
            "AND parlevel = 0 AND paristemplate = false)"
        )
        return ", ".join(r["attname"] for r in rows)

    def _rows(self, schema: str, table: str) -> int:
        """reltuples; for a partitioned root (reltuples 0) sum the partition children."""
        target = f"'{schema}.{_ident(table)}'::regclass"
        own = self._run(f"SELECT c.reltuples::bigint AS n FROM pg_class c WHERE c.oid = {target}")
        rows = int(own[0]["n"]) if own else 0
        if rows > 0:
            return rows
        children = self._run(
            "SELECT COALESCE(sum(child.reltuples), 0)::bigint AS n FROM pg_inherits i "
            f"JOIN pg_class child ON child.oid = i.inhrelid WHERE i.inhparent = {target}"
        )
        return int(children[0]["n"]) if children else 0

    def _cardinality(self, schema: str, table: str) -> dict[str, int]:
        """Distinct counts from pg_stats (post-ANALYZE): n_distinct<0 is a row fraction."""
        rows = self._run(
            "SELECT attname, n_distinct FROM pg_stats "
            f"WHERE schemaname = '{schema}' AND tablename = '{_ident(table)}'"
        )
        if not rows:
            return {}
        total = self._rows(schema, table)
        out: dict[str, int] = {}
        for r in rows:
            nd = float(r["n_distinct"] or 0)
            out[r["attname"]] = int(nd) if nd >= 0 else round(-nd * total)
        return out

    def _top_values(
        self,
        schema: str,
        table: str,
        column: str,
        role: ColumnRole,
        cardinality: dict[str, int],
    ) -> list[str]:
        if role != ColumnRole.DIMENSION or column.endswith("_id") or column == "id":
            return []
        uniq = cardinality.get(column, LOW_CARDINALITY_MAX + 1)
        if uniq == 0 or uniq > LOW_CARDINALITY_MAX:
            return []
        result = self._run(
            f'SELECT "{_ident(column)}"::text AS v, count(*) AS cnt '
            f'FROM "{schema}"."{_ident(table)}" GROUP BY v ORDER BY cnt DESC LIMIT 20'
        )
        return [r["v"] for r in result if r["v"] is not None]


def make_run_query_pg(settings: Settings) -> RunQuery:
    """Real psycopg client behind the RunQuery seam (read-only role, one session).

    One persistent connection so a `SET statement_timeout` issued by LiveSQLValidator
    persists for the trial SELECT that follows. Statements with no result set (SET) -> []."""
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(
        host=settings.gp_host,
        port=settings.gp_port,
        dbname=settings.gp_database,
        user=settings.gp_user,
        password=settings.gp_password or None,
        autocommit=True,
        row_factory=dict_row,
    )

    def run(sql: str) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type]
            if cur.description is None:
                return []
            return list(cur.fetchall())

    return run
