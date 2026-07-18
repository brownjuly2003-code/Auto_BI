"""X-5 raw_sql escape hatch: a manual SELECT bypassing IR, gated live like generated SQL.

Covers the deterministic contract around the hatch: the IR accepts a measureless raw chart (and
still rejects a measureless non-raw one), SQL_GEN returns the SQL verbatim, validate_spec restricts
the hatch to a single SELECT + viz=TABLE with no aggregating IR fields alongside it, the Superset
table renders in raw query mode, and the moat layers (top-N/label-join normalization, advisor)
skip it. P0-1: LLM path rejects raw_sql; schema-RBAC walks the SELECT AST; DataLens is rejected.
The live EXPLAIN + LIMIT trial is exercised by the stand e2e, not here.
"""

import pytest
from pydantic import ValidationError

from auto_bi.adapters.superset.form_data import build_form_data
from auto_bi.advisor.core import Advisor
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sql_guard import SQLGuardError, extract_table_names, guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.auth import forbidden_tables, spec_tables
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    Measure,
    TargetBI,
    Viz,
    llm_dashboard_spec_schema,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Aggregation, SemanticModel

MODEL = SemanticModel.load("semantic/model.yaml")
RAW = "SELECT store_id, SUM(revenue) AS rev FROM dm.sales_daily GROUP BY store_id"


def _raw_chart(
    sql: str = RAW, *, viz: Viz = Viz.TABLE, columns: list[str] | None = None
) -> ChartSpec:
    return ChartSpec(
        id="raw",
        title="Raw",
        viz=viz,
        query=ChartQuery(table="dm.sales_daily", dimensions=columns or [], raw_sql=sql),
    )


def _raw_spec(**kw) -> DashboardSpec:
    return DashboardSpec(title="Raw", charts=[_raw_chart(**kw)])


# --- IR field contract ----------------------------------------------------


def test_raw_query_needs_no_measure() -> None:
    q = ChartQuery(table="dm.sales_daily", raw_sql=RAW)
    assert q.raw_sql == RAW
    assert q.measures == []


def test_non_raw_query_still_requires_a_measure() -> None:
    with pytest.raises(ValidationError):
        ChartQuery(table="dm.sales_daily")


def test_empty_raw_sql_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ChartQuery(table="dm.sales_daily", raw_sql="   ")


# --- SQL_GEN --------------------------------------------------------------


def test_generate_chart_sql_returns_raw_verbatim() -> None:
    q = ChartQuery(table="dm.sales_daily", raw_sql=RAW)
    assert generate_chart_sql(q) == RAW
    # the dialect never re-renders it (it is already written for the target engine)
    assert generate_chart_sql(q, dialect="postgres") == RAW


# --- validate_spec --------------------------------------------------------


def test_validate_accepts_raw_table() -> None:
    assert validate_spec(_raw_spec(columns=["store_id", "rev"]), MODEL) == []


def test_validate_rejects_raw_non_table() -> None:
    errors = validate_spec(_raw_spec(viz=Viz.BAR), MODEL)
    assert any("only with viz=table" in e for e in errors)


def test_validate_rejects_raw_with_ir_fields() -> None:
    chart = ChartSpec(
        id="raw",
        title="Raw",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.sales_daily",
            raw_sql=RAW,
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )
    errors = validate_spec(DashboardSpec(title="t", charts=[chart]), MODEL)
    assert any("cannot be combined with IR query fields" in e and "measures" in e for e in errors)


def test_validate_rejects_non_select_raw() -> None:
    errors = validate_spec(_raw_spec(sql="DELETE FROM dm.sales_daily"), MODEL)
    assert any("not a single plain SELECT" in e for e in errors)


def test_validate_raw_table_need_not_exist_in_model() -> None:
    # the SQL names its own tables; the chart's `table` is only a dataset label and need not
    # exist in the model (a raw query may join marts the model does not describe)
    spec = DashboardSpec(
        title="t",
        charts=[
            ChartSpec(
                id="raw",
                title="Raw",
                viz=Viz.TABLE,
                query=ChartQuery(table="whatever.unknown", raw_sql=RAW),
            )
        ],
    )
    assert validate_spec(spec, MODEL) == []


# --- Superset form_data ---------------------------------------------------


def test_form_data_raw_uses_raw_query_mode() -> None:
    fd = build_form_data(_raw_chart(columns=["store_id", "rev"]), dataset_id=7)
    assert fd["query_mode"] == "raw"
    assert fd["all_columns"] == ["store_id", "rev"]
    assert fd["viz_type"] == "table"
    assert "groupby" not in fd and "metrics" not in fd


def test_form_data_raw_without_columns_shows_all() -> None:
    fd = build_form_data(_raw_chart(), dataset_id=7)
    assert fd["query_mode"] == "raw"
    assert "all_columns" not in fd  # empty => Superset renders every column of the result


# --- moat layers skip raw -------------------------------------------------


def test_normalization_leaves_raw_untouched() -> None:
    spec = _raw_spec(columns=["store_id", "rev"])
    assert apply_label_joins(spec, MODEL) == spec
    assert apply_chart_defaults(spec, MODEL) == spec


def test_advisor_is_blind_to_raw() -> None:
    assert Advisor(MODEL).review_chart(_raw_chart()) == []


# --- P0-1: operator-only hatch + RBAC on AST ---------------------------------


def test_llm_schema_excludes_raw_sql() -> None:
    schema = llm_dashboard_spec_schema()
    defs = schema.get("$defs") or schema.get("definitions") or {}
    props = defs["ChartQuery"]["properties"]
    assert "raw_sql" not in props
    # runtime IR still carries the field for the CLI hatch
    assert "raw_sql" in ChartQuery.model_json_schema()["properties"]


def test_validate_rejects_raw_on_llm_path() -> None:
    errors = validate_spec(_raw_spec(columns=["store_id", "rev"]), MODEL, allow_raw_sql=False)
    assert any("operator-only hatch" in e for e in errors)


def test_validate_rejects_raw_on_datalens() -> None:
    spec = DashboardSpec(
        title="Raw",
        target_bi=TargetBI.DATALENS,
        charts=[_raw_chart(columns=["store_id", "rev"])],
    )
    errors = validate_spec(spec, MODEL)
    assert any("target_bi=superset" in e for e in errors)


def test_extract_table_names_from_raw_sql() -> None:
    assert extract_table_names(RAW) == frozenset({"dm.sales_daily"})
    with_cte = (
        "WITH base AS (SELECT store_id, revenue FROM dm.sales_daily) "
        "SELECT store_id, SUM(revenue) AS rev FROM base GROUP BY store_id"
    )
    assert extract_table_names(with_cte) == frozenset({"dm.sales_daily"})


def test_guard_rejects_remote_table_function() -> None:
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql("SELECT * FROM url('http://evil.example/x')")


# C-1: remote/RBAC-blind ClickHouse table functions, in the camelCase spelling the
# ClickHouse docs use (the guard compares lowercased names).
_C1_TABLE_FUNCS = [
    "remoteSecure",
    "s3Cluster",
    "hdfsCluster",
    "urlCluster",
    "fileCluster",
    "azureBlobStorage",
    "azureBlobStorageCluster",
    "gcs",
    "oss",
    "deltaLake",
    "deltaLakeCluster",
    "iceberg",
    "icebergCluster",
    "hudi",
    "hudiCluster",
    "dictionary",
    "executable",
    "merge",
]


@pytest.mark.parametrize("func", _C1_TABLE_FUNCS)
def test_guard_rejects_rbac_blind_table_functions(func: str) -> None:
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql(f"SELECT * FROM {func}('a', 'b')")


def test_guard_allows_schema_qualified_table_named_like_function() -> None:
    # dm.dictionary is a real table read, not the dictionary() table function
    guard_sql("SELECT * FROM dm.dictionary")


def test_guard_allows_cte_alias_shadowing_denylisted_name() -> None:
    guard_sql("WITH merge AS (SELECT 1 AS x) SELECT x FROM merge")


def test_validate_rejects_merge_in_raw_sql_path() -> None:
    # merge('db','regexp') reads every table matching the regexp with the service
    # account's rights — the hatch must reject it at validation, before any live trial.
    errors = validate_spec(_raw_spec(sql="SELECT * FROM merge('dm', '^sales')"), MODEL)
    assert any("forbidden table function in SQL: merge()" in e for e in errors)


def test_spec_tables_walks_raw_sql_ast() -> None:
    # Label says dm.allowed, SQL reads finance.secret — RBAC must see the real table.
    chart = ChartSpec(
        id="raw",
        title="Raw",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.allowed",
            dimensions=["x"],
            raw_sql="SELECT id, amount FROM finance.secret",
        ),
    )
    spec = DashboardSpec(title="t", charts=[chart])
    assert "finance.secret" in spec_tables(spec)
    assert "dm.allowed" in spec_tables(spec)
    assert forbidden_tables(spec, ["dm"]) == ["finance.secret"]
    assert forbidden_tables(spec, ["dm", "finance"]) == []


def test_operator_raw_still_validates() -> None:
    # CLI path keeps allow_raw_sql=True (default): hatch still works for operators.
    assert validate_spec(_raw_spec(columns=["store_id", "rev"]), MODEL) == []
