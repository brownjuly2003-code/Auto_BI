"""Collecting §8.2 step 7's facts from a running engine — the IO half of `environment.py`.

Everything here goes through `RunQuery`, the read-only callable introspection, the SQL guard
and the advisor already share: one seam, one place a real client is built, and a fake in
tests instead of a socket. Nothing in this module decides anything — it asks, normalises and
hands typed facts back for the pure rules to grade.

**The gate never probes by writing.** Whether the session could write is read out of the
engine's own answer, because an INSERT "just to see" is a write to somebody's warehouse, and
a gate that mutates the thing it verifies has already failed invariant 3.

Each engine is asked the question that is true *for it*, and both forms were checked on
stands of the pinned versions:

* **ClickHouse 24.8.14.39** — a user declared in `users.xml` holds `GRANT INSERT, ALTER,
  DROP … ON *.*` even when its profile sets `readonly=1`, and it is the profile that makes
  the session refuse (`Code: 164 READONLY`). So the setting decides, and grants are read only
  to catch the other case: `readonly=0` with no write privilege granted is read-only too.
  `max(Date)` over an empty table answers `1970-01-01` rather than NULL, so the row count
  travels with the reading — otherwise an empty mart reads as fifty years stale.
* **PostgreSQL 14.24** (the `numeric`/privilege behaviour Greenplum and Greengage inherit) —
  `transaction_read_only` is `off` for a SELECT-only role, so the session GUC says nothing;
  `has_table_privilege` per in-scope mart is the answer, and a superuser is writable
  whatever the grants say, because privileges do not apply to it. `max()` over no rows is
  NULL here, which the fact model already allows for.

Column types come from each engine's own catalog in the spelling the model was written in —
`system.columns.type` on ClickHouse, `format_type(atttypid, atttypmod)` on the Postgres
family, which is the same function the Greenplum introspector wrote those types with, so the
comparison is between two spellings of one thing rather than between two conventions.

**Layout is reported only where it was verified.** ClickHouse answers engine, sorting key and
partition key from `system.tables`, and the stand confirmed the shapes (`MergeTree`,
`date, store_id`, `toYYYYMM(date)`). Greenplum keeps its distribution policy and partition
levels in catalogs no PostgreSQL stand has (`gp_distribution_policy`, `pg_partition`), and no
Greengage stand has been available to pin their columns — so the Postgres path leaves those
facts unreported, and `check_physical` compares nothing rather than guessing at a mismatch.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from auto_bi.engine import CLICKHOUSE, GREENPLUM, sqlglot_dialect
from auto_bi.introspect.base import RunQuery
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    LayoutFacts,
    RoleFacts,
    TableFacts,
)

#: Privileges that make a session capable of changing a mart. `TRUNCATE` is Postgres-only;
#: the ClickHouse path reads its own access types.
_PG_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

#: ClickHouse access types that can change data or schema, as `system.grants` spells them.
_CH_WRITE_GRANTS = frozenset(
    {"INSERT", "ALTER", "CREATE", "DROP", "TRUNCATE", "OPTIMIZE", "UNDROP TABLE"}
)


def collect(
    run_query: RunQuery, questions: EnvironmentQuestions, *, engine: str, target: str
) -> EnvironmentFacts:
    """Ask one engine everything step 7 needs to know about one environment."""
    dialect = sqlglot_dialect(engine)
    if dialect == sqlglot_dialect(GREENPLUM):
        role = _postgres_role(run_query, questions)
        tables = [
            _postgres_table(run_query, item.table, item.event_time_column)
            for item in questions.tables
        ]
    else:
        role = _clickhouse_role(run_query)
        tables = [
            _clickhouse_table(run_query, item.table, item.event_time_column)
            for item in questions.tables
        ]
    return EnvironmentFacts(engine=engine, target=target, role=role, tables=tables)


# --- ClickHouse ---------------------------------------------------------------


def _clickhouse_role(run_query: RunQuery) -> RoleFacts:
    """`readonly` decides; grants are the second chance to be read-only, not the first."""
    name = _scalar(run_query, "SELECT currentUser() AS value") or "unknown"
    setting = _scalar(run_query, "SELECT value FROM system.settings WHERE name = 'readonly'")
    if setting in {"1", "2"}:
        return RoleFacts(
            name=name,
            read_only=True,
            evidence=f"readonly={setting}, so the session refuses writes whatever it is granted",
        )
    granted = {
        str(row.get("access_type", ""))
        for row in _rows(
            run_query,
            "SELECT access_type FROM system.grants WHERE user_name = currentUser()",
        )
    }
    writable = sorted(granted & _CH_WRITE_GRANTS)
    return RoleFacts(
        name=name,
        read_only=not writable,
        evidence=(
            f"readonly={setting or 'unset'}, granted {', '.join(writable)}"
            if writable
            else f"readonly={setting or 'unset'}, no write access type granted"
        ),
    )


def _clickhouse_table(run_query: RunQuery, table: str, event_time_column: str) -> TableFacts:
    database, _, name = table.partition(".")
    present = _rows(
        run_query,
        "SELECT engine, sorting_key, partition_key FROM system.tables WHERE database = "
        f"{_literal(database)} AND name = {_literal(name)}",
    )
    if not present:
        return TableFacts(table=table, exists=False, readable=False)
    reading = _rows(
        run_query,
        f"SELECT count() AS rows_seen, max({_ident(event_time_column)}) AS max_event_time "
        f"FROM {_qualified(table)}",
    )
    if not reading:  # the table is there but the role cannot read it
        return TableFacts(table=table, exists=True, readable=False)
    columns = {
        str(row.get("name")): str(row.get("type"))
        for row in _rows(
            run_query,
            "SELECT name, type FROM system.columns WHERE database = "
            f"{_literal(database)} AND table = {_literal(name)}",
        )
    }
    layout = LayoutFacts(
        table_engine=str(present[0].get("engine") or ""),
        sorting_key=_key_list(present[0].get("sorting_key")),
        partition_key=str(present[0].get("partition_key") or ""),
        # ClickHouse has no distribution policy to report; None is "not reported", and
        # `check_physical` compares only what an engine answered.
        distribution_key=None,
    )
    return _reading(table, reading[0], columns=columns, layout=layout)


# --- PostgreSQL family --------------------------------------------------------


def _postgres_role(run_query: RunQuery, questions: EnvironmentQuestions) -> RoleFacts:
    """A superuser bypasses privileges, so it is writable however the grants read."""
    rows = _rows(
        run_query,
        "SELECT current_user AS name, "
        "(SELECT usesuper FROM pg_user WHERE usename = current_user) AS superuser",
    )
    name = str(rows[0].get("name")) if rows else "unknown"
    if rows and bool(rows[0].get("superuser")):
        return RoleFacts(
            name=name,
            read_only=False,
            evidence="a superuser, whose writes no privilege check can stop",
        )

    writable: list[str] = []
    for question in questions.tables:
        privileges = ", ".join(
            f"has_table_privilege({_literal(question.table)}, {_literal(privilege)}) AS "
            f"{privilege.lower()}"
            for privilege in _PG_WRITE_PRIVILEGES
        )
        granted = _rows(run_query, f"SELECT {privileges}")
        if not granted:
            continue
        for privilege in _PG_WRITE_PRIVILEGES:
            if bool(granted[0].get(privilege.lower())):
                writable.append(f"{privilege} on {question.table}")
    return RoleFacts(
        name=name,
        read_only=not writable,
        evidence=(
            f"granted {'; '.join(writable)}"
            if writable
            else "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart"
        ),
    )


def _postgres_table(run_query: RunQuery, table: str, event_time_column: str) -> TableFacts:
    schema, _, name = table.partition(".")
    present = _rows(
        run_query,
        "SELECT tablename FROM pg_tables WHERE schemaname = "
        f"{_literal(schema)} AND tablename = {_literal(name)} "
        "UNION ALL SELECT viewname FROM pg_views WHERE schemaname = "
        f"{_literal(schema)} AND viewname = {_literal(name)}",
    )
    if not present:
        return TableFacts(table=table, exists=False, readable=False)
    if not _rows(run_query, f"SELECT has_table_privilege({_literal(table)}, 'SELECT') AS ok")[
        0
    ].get("ok"):
        return TableFacts(table=table, exists=True, readable=False)
    reading = _rows(
        run_query,
        f"SELECT count(*) AS rows_seen, max({_ident(event_time_column)}) AS max_event_time "
        f"FROM {_qualified(table)}",
    )
    if not reading:
        return TableFacts(table=table, exists=True, readable=False)
    columns = {
        str(row.get("name")): str(row.get("type"))
        for row in _rows(
            run_query,
            "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type "
            "FROM pg_attribute a WHERE a.attrelid = "
            f"to_regclass({_literal(table)}) AND a.attnum > 0 AND NOT a.attisdropped",
        )
    }
    # Layout stays unreported here: the distribution policy and partition levels live in
    # Greenplum-only catalogs, and no Greengage stand has been available to pin them.
    return _reading(table, reading[0], columns=columns, layout=None)


# --- shared -------------------------------------------------------------------


def _reading(
    table: str,
    row: dict[str, Any],
    *,
    columns: dict[str, str] | None = None,
    layout: LayoutFacts | None = None,
) -> TableFacts:
    rows_seen = int(row.get("rows_seen") or 0)
    return TableFacts(
        table=table,
        exists=True,
        readable=True,
        rows_seen=rows_seen,
        # An empty mart has nothing to date; ClickHouse would still hand back the epoch.
        max_event_time=_moment(row.get("max_event_time")) if rows_seen else None,
        columns=columns,
        layout=layout,
    )


def _key_list(value: object) -> list[str]:
    """`date, store_id` as ClickHouse prints a sorting key -> the columns it names."""
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _moment(value: object) -> str | None:
    """Engine value as ISO 8601 — a date stays a date, a datetime keeps its offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _scalar(run_query: RunQuery, sql: str) -> str | None:
    rows = _rows(run_query, sql)
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    return None if value is None else str(value)


def _rows(run_query: RunQuery, sql: str) -> list[dict[str, Any]]:
    return list(run_query(sql))


def _literal(value: str) -> str:
    """A single-quoted literal with quotes doubled — these are catalog lookups, not user SQL."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _qualified(table: str) -> str:
    return ".".join(_ident(part) for part in table.split("."))


__all__ = ["CLICKHOUSE", "collect"]
