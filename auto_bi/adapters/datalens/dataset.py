"""IR -> DataLens createConnection / createDataset payloads (deterministic).

The shapes here are LIVE-VERIFIED on the self-hosted stand (reversal doc
`docs/plans/2026-06-13-phase3.2-datalens-adapter-reversal.md` §3-4): both
`bi/createConnection` and `bi/createDataset` returned 200 with these bodies.

Key design point (invariant 1, and reversal §4): DataLens normally derives a SQL
source's column schema by introspecting the DB through `validateDataset` — but that
gateway action drops its body (415). We don't need it: the adapter already knows the
subselect's columns and their roles from the IR (`ChartQuery` dimensions/measures), so
it builds `raw_schema` + `result_schema` deterministically. That is strictly more in
line with invariant 1 than DB introspection, and needs no live stand to produce — only
the live contract test confirms the numbers match a direct DWH query.

One validated-SQL dataset per chart (mirrors SupersetAdapter.ensure_dataset): the
subselect is exactly the chart's grain, so re-aggregation in the chart is the identity
(see `_field_aggregation`).
"""

from __future__ import annotations

import hashlib
import re
import uuid

from auto_bi.adapters.base import DWHConfig
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.engine import sqlglot_dialect
from auto_bi.ir.spec import ChartQuery, Measure, column_alias, measure_alias
from auto_bi.semantic.model import Aggregation, SemanticModel, Table

# physical.engine / DWHConfig.engine -> DataLens connection `type` (input field).
# NB: the type field on input is `type`, NOT `db_type` (db_type is only in getConnection
# responses) — reversal §3.
_CONNECTION_TYPE = {
    "clickhouse": "clickhouse",
    "greenplum": "greenplum",
    "greengage": "greenplum",
    "postgres": "greenplum",
    "postgresql": "greenplum",
}

# engine -> DataLens subselect source_type (reversal §4).
_SOURCE_TYPE = {
    "clickhouse": "CH_SUBSELECT",
    "greenplum": "PG_SUBSELECT",
    "greengage": "PG_SUBSELECT",
    "postgres": "PG_SUBSELECT",
    "postgresql": "PG_SUBSELECT",
}

# Fixed namespace so source/avatar/field ids are stable across runs (idempotency) and
# deterministic in tests. Not a secret; any constant UUID works.
_NS = uuid.UUID("a7b0c1d2-e3f4-5061-7283-94a5b6c7d8e9")


def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_")[:max_len] or "dataset"


def dataset_name(title: str, chart_id: str) -> str:
    """Readable, collision-free dataset name (mirrors SupersetAdapter._dataset_name)."""
    suffix = hashlib.sha1(chart_id.encode()).hexdigest()[:8]
    return f"auto_bi__{_slug(title)}__{_slug(chart_id)}__{suffix}"


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "::".join(parts)))


def _user_type(native_type: str) -> str:
    """Native DWH type string -> DataLens UserDataType (the 6 common values).

    Handles both ClickHouse (`Nullable(String)`, `UInt32`, `Decimal(18,2)`,
    `LowCardinality(String)`, `DateTime`) and PostgreSQL/Greenplum (`integer`, `bigint`,
    `numeric`, `double precision`, `timestamp without time zone`, `boolean`) spellings.
    """
    t = native_type.strip().lower()
    # unwrap ClickHouse modifier wrappers: Nullable(...), LowCardinality(...)
    while (m := re.match(r"^(?:nullable|lowcardinality)\((.*)\)$", t)) is not None:
        t = m.group(1).strip()
    if "datetime" in t or "timestamp" in t:
        return "genericdatetime"  # DataLens DATASET_FIELD_TYPES enum (NOT "datetime")
    if "date" in t:
        return "date"
    if "bool" in t:
        return "boolean"
    if any(k in t for k in ("float", "double", "real", "decimal", "numeric")):
        return "float"
    if "int" in t:  # int, integer, bigint, smallint, uint8/16/32/64
        return "integer"
    return "string"


def _measure_user_type(measure: Measure, base_table: Table | None) -> str:
    """Result type of an aggregated measure column."""
    if measure.agg in (Aggregation.COUNT, Aggregation.COUNT_DISTINCT):
        return "integer"
    if measure.agg == Aggregation.AVG:
        return "float"
    # sum/min/max preserve the source column's numeric family
    col = base_table.column(measure.column) if base_table else None
    return _user_type(col.type) if col is not None else "float"


def _resolve_column_type(model: SemanticModel, base_table_name: str, ref: str) -> str:
    """user_type of a dimension-like reference (qualified `dm.stores.city` or bare)."""
    table_part, _, col_name = ref.rpartition(".")
    table = model.table(table_part) if table_part else model.table(base_table_name)
    if table is None:  # qualified to an unknown table -> fall back to the base table
        table = model.table(base_table_name)
        col_name = ref
    col = table.column(col_name) if table is not None else None
    return _user_type(col.type) if col is not None else "string"


def _engine_of(model: SemanticModel, base_table_name: str) -> str:
    table = model.table(base_table_name)
    if table is not None and table.physical is not None:
        return table.physical.engine.lower()
    return "clickhouse"


def build_connection_payload(
    dwh: DWHConfig,
    name: str,
    workbook_id: str,
    *,
    secure: str = "off",
    raw_sql_level: str = "subselect",
    cache_ttl_sec: int | None = None,
) -> dict:
    """`bi/createConnection` body (reversal §3). `raw_sql_level="subselect"` is REQUIRED
    for dataset-from-SQL. `secure` is a string flag: "off" for plain CH :8123, "on" for
    HTTPS/TLS."""
    conn_type = _CONNECTION_TYPE.get(dwh.engine.lower())
    if conn_type is None:
        raise ValueError(f"no DataLens connection type for engine {dwh.engine!r}")
    return {
        "name": name,
        "workbook_id": workbook_id,
        "type": conn_type,
        "host": dwh.host,
        "port": dwh.port,
        "username": dwh.user,
        "password": dwh.password,
        "secure": secure,
        "raw_sql_level": raw_sql_level,
        "cache_ttl_sec": cache_ttl_sec,
    }


def _field_aggregation(is_measure: bool) -> str:
    """DataLens dataset-field aggregation.

    Measures re-aggregate the pre-grouped subselect with "sum" — the identity over the
    single row per group, mirroring SupersetAdapter's uniform SUM re-aggregation
    (form_data.py `_adhoc_metric`). Each chart's dataset is its exact grain, so the
    chart's group-by equals the dataset grain and any aggregation is the identity; "sum"
    is chosen for robustness (a COUNT measure must NOT re-count to 1). The live contract
    test confirms the numbers match a direct DWH query.
    """
    return "sum" if is_measure else "none"


def build_dataset_payload(
    query: ChartQuery,
    model: SemanticModel,
    *,
    workbook_id: str,
    connection_id: str,
    name: str,
    source_title: str | None = None,
    apply_limit: bool = True,
) -> dict:
    """`bi/createDataset` body (reversal §4): one subselect source, columns + roles from
    the IR. No DB introspection / validateDataset needed.

    `apply_limit=False` drops the subselect's top-N LIMIT for charts in a dashboard
    selector's scope, so the selector re-ranks after filtering and its option list isn't
    capped to the pre-filter top-N (mirrors the Superset native-filter limit semantics)."""
    engine = _engine_of(model, query.table)
    source_type = _SOURCE_TYPE.get(engine)
    if source_type is None:
        raise ValueError(f"no DataLens source_type for engine {engine!r}")
    sql = generate_chart_sql(query, dialect=sqlglot_dialect(engine), apply_limit=apply_limit)
    src_title = source_title or name

    base_table = model.table(query.table)
    # SELECT-order columns: dimension-like group columns first, then measures — the same
    # order generate_chart_sql emits, addressed by their bare aliases (column_alias).
    fields: list[tuple[str, str, bool]] = []  # (alias, user_type, is_measure)
    for ref in query.group_columns():
        fields.append((column_alias(ref), _resolve_column_type(model, query.table, ref), False))
    for measure in query.measures:
        fields.append((measure_alias(measure), _measure_user_type(measure, base_table), True))

    source_id = _stable_uuid(name, "source")
    avatar_id = _stable_uuid(name, "avatar")

    raw_schema = [
        {
            "name": alias,
            "title": alias,
            "user_type": user_type,
            "native_type": None,
            "nullable": True,
        }
        for alias, user_type, _ in fields
    ]
    result_schema = [
        {
            "guid": _stable_uuid(name, "field", alias),
            "title": alias,
            "source": alias,
            "data_type": user_type,
            "cast": user_type,
            "type": "MEASURE" if is_measure else "DIMENSION",
            "aggregation": _field_aggregation(is_measure),
            "calc_mode": "direct",
            "avatar_id": avatar_id,
            "hidden": False,
            "managed_by": "user",
            "description": "",
            "formula": "",
            "valid": True,
        }
        for alias, user_type, is_measure in fields
    ]

    return {
        # snake_case: control-api reads the workbook from `workbook_id` in the body — a
        # camelCase `workbookId` is silently ignored and the dataset is created orphaned
        # (workbook_id NULL), which then makes data-api 403 ACCESS_DENIED at render time
        # (live-verified 2026-06-14).
        "workbook_id": workbook_id,
        "name": name,
        "dataset": {
            "sources": [
                {
                    "id": source_id,
                    "title": src_title,
                    "connection_id": connection_id,
                    "source_type": source_type,
                    "parameters": {"subsql": sql},
                    "managed_by": "user",
                    "raw_schema": raw_schema,
                    "index_info_set": [],
                }
            ],
            "source_avatars": [
                {
                    "id": avatar_id,
                    "source_id": source_id,
                    "title": src_title,
                    "is_root": True,
                    "managed_by": "user",
                }
            ],
            "avatar_relations": [],
            "result_schema": result_schema,
            "rls": {},
            "component_errors": {"items": []},
        },
    }
