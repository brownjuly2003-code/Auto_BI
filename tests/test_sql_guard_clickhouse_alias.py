"""A ClickHouse alias is read through, not as a column (T-13).

sqlglot reports a bare alias as a Column of the query's table and does not put
the columns of an expression-form `WITH <expr> AS a` into any scope. The guard
must skip the alias name and, when the alias is a referenced expression-form
WITH, grade the columns written in `<expr>` at the referencing scope.
PostgreSQL is unchanged: WITH is CTEs only, and a SELECT alias in WHERE is a
column.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import pytest

from auto_bi.agent.sql_guard import (
    CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET,
    CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH,
    AmbiguousRef,
    ColumnReads,
    extract_column_refs,
    extract_table_refs,
)
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact import (
    DASHBOARD,
    _relabelled,
    operator_dashboard,
    resolved,
)
from tests.test_decision_chart_impact_dictionary import charts, field_change
from tests.test_semantic_diff import snap
from tests.test_verified_assets import asset, codes, query, repository

FLOOR_VALUE = (
    "WITH 100 AS floor_value SELECT region, sum(revenue) AS m FROM dm.sales_daily "
    "WHERE revenue > floor_value GROUP BY region"
)
ALLOWED = (
    "WITH ['north', 'south'] AS allowed SELECT region, sum(revenue) AS m "
    "FROM dm.sales_daily WHERE region IN allowed GROUP BY region"
)
SELECT_ALIAS_IN_SELECT = (
    "SELECT region, sum(revenue) AS m, m * 2 AS m2 FROM dm.sales_daily GROUP BY region"
)
SELECT_ALIAS_IN_WHERE = (
    "SELECT region AS r, sum(revenue) FROM dm.sales_daily WHERE r != '' GROUP BY r"
)
READ_THROUGH = (
    "WITH internal_cost_raw * 2 AS c2 SELECT region, sum(c2) FROM dm.sales_daily GROUP BY region"
)


def attributed(sql: str, *, dialect: str = "clickhouse") -> list[str]:
    reads = extract_column_refs(sql, dialect=dialect)
    return sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed)


def unattributed(sql: str, *, dialect: str = "clickhouse") -> list[str]:
    return sorted(extract_column_refs(sql, dialect=dialect).unattributed)


def ambiguous(sql: str, *, dialect: str = "clickhouse") -> frozenset[AmbiguousRef]:
    return extract_column_refs(sql, dialect=dialect).ambiguous


def ungoverned_codes(tmp_path: Path, sql: str, *, engine: str = "clickhouse") -> list[str]:
    return codes(
        repository(tmp_path / "repo", asset(reference_query=query(sql, engine=engine))).ungoverned(
            snap(tmp_path, "base")
        )
    )


def ungoverned_detail(tmp_path: Path, sql: str, *, engine: str = "clickhouse") -> str:
    items = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(snap(tmp_path, "base"))
    return " | ".join(item.detail for item in items)


def ungoverned_pairs(
    tmp_path: Path, sql: str, *, engine: str = "clickhouse"
) -> list[tuple[str, str]]:
    items = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(snap(tmp_path, "base"))
    return [(item.code, item.severity.value) for item in items]


def assert_with_matches_inline(with_sql: str, inline_sql: str) -> tuple[ColumnReads, ColumnReads]:
    with_reads = extract_column_refs(with_sql, dialect="clickhouse")
    inline_reads = extract_column_refs(inline_sql, dialect="clickhouse")
    assert with_reads.attributed == inline_reads.attributed
    assert with_reads.ambiguous == inline_reads.ambiguous
    assert with_reads.unattributed == inline_reads.unattributed
    return with_reads, inline_reads


# --- false-reject control: valid reviewed SQL of declared columns stays clean ---------------


def test_a_reviewed_query_over_declared_columns_is_still_clean(tmp_path: Path) -> None:
    """Tightening alias handling must not reject a valid reference query."""
    sql = "SELECT region, sum(revenue) AS m FROM dm.sales_daily GROUP BY region"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_extract_table_refs_is_unchanged_for_a_scalar_with() -> None:
    assert sorted(ref.name for ref in extract_table_refs(FLOOR_VALUE, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]


# --- the five reproductions -----------------------------------------------------------------


def test_a_literal_with_alias_in_where_is_not_a_column(tmp_path: Path) -> None:
    assert attributed(FLOOR_VALUE) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(FLOOR_VALUE) == []
    assert ungoverned_codes(tmp_path, FLOOR_VALUE) == []


def test_a_with_alias_on_the_right_of_in_is_not_a_column(tmp_path: Path) -> None:
    """`IN <alias>` is an alias (rule 1); `IN <table>` is the next task."""
    assert attributed(ALLOWED) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(ALLOWED) == []
    assert ungoverned_codes(tmp_path, ALLOWED) == []


def test_a_select_alias_used_in_another_select_expression_is_not_a_column(
    tmp_path: Path,
) -> None:
    assert attributed(SELECT_ALIAS_IN_SELECT) == [
        "dm.sales_daily.region",
        "dm.sales_daily.revenue",
    ]
    assert unattributed(SELECT_ALIAS_IN_SELECT) == []
    assert ungoverned_codes(tmp_path, SELECT_ALIAS_IN_SELECT) == []


def test_a_select_alias_in_where_and_group_by_is_not_a_column(tmp_path: Path) -> None:
    assert attributed(SELECT_ALIAS_IN_WHERE) == [
        "dm.sales_daily.region",
        "dm.sales_daily.revenue",
    ]
    assert unattributed(SELECT_ALIAS_IN_WHERE) == []
    assert ungoverned_codes(tmp_path, SELECT_ALIAS_IN_WHERE) == []


def test_a_referenced_expression_with_is_read_through(tmp_path: Path) -> None:
    """The alias is not a column; the undeclared field the expression reads is."""
    assert attributed(READ_THROUGH) == [
        "dm.sales_daily.internal_cost_raw",
        "dm.sales_daily.region",
    ]
    assert unattributed(READ_THROUGH) == []
    assert ungoverned_codes(tmp_path, READ_THROUGH) == ["asset.query_ungoverned_field"]
    detail = ungoverned_detail(tmp_path / "detail", READ_THROUGH)
    assert "dm.sales_daily.internal_cost_raw" in detail
    assert "c2" not in detail


# --- self-reference, shadowing, visibility, case, chain -------------------------------------


def test_an_alias_self_reference_still_reads_the_column(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue) AS revenue FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.revenue"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_visible_alias_wins_over_a_table_column_of_the_same_name(tmp_path: Path) -> None:
    sql = "WITH 1 AS region SELECT region FROM dm.sales_daily"
    assert attributed(sql) == []
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_select_alias_is_not_visible_in_a_subquery(tmp_path: Path) -> None:
    sql = "SELECT region AS r FROM dm.sales_daily WHERE (SELECT r FROM dm.stores LIMIT 1) != ''"
    assert attributed(sql) == ["dm.sales_daily.region"]
    assert unattributed(sql) == ["r"]
    enclosed = {ref.name: ref for ref in ambiguous(sql)}
    assert enclosed["r"].enclosed is True
    assert [c.name for c in enclosed["r"].candidates] == ["dm.stores"]
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_fields_unchecked"]
    assert "r" in ungoverned_detail(tmp_path / "detail", sql)


def test_a_with_alias_is_visible_in_a_subquery(tmp_path: Path) -> None:
    sql = (
        "WITH 1 AS k SELECT region FROM dm.sales_daily WHERE (SELECT k FROM dm.stores LIMIT 1) != 0"
    )
    assert attributed(sql) == ["dm.sales_daily.region"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_an_unreferenced_expression_with_reads_nothing(tmp_path: Path) -> None:
    sql = "WITH internal_cost_raw * 2 AS c2 SELECT region FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.region"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_clickhouse_alias_names_are_case_sensitive(tmp_path: Path) -> None:
    sql = "WITH 1 AS k SELECT K FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.K"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.K" in ungoverned_detail(tmp_path / "detail", sql)
    assert attributed("WITH 1 AS k SELECT k FROM dm.sales_daily") == []


def test_a_with_alias_chain_is_read_through_without_following_a_cycle() -> None:
    sql = "WITH internal_cost_raw * 2 AS c2, c2 + 1 AS c3 SELECT c3 FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.internal_cost_raw"]
    assert unattributed(sql) == []


def test_a_with_alias_referenced_from_two_levels_is_graded_at_each() -> None:
    sql = (
        "WITH internal_cost_raw * 2 AS c2 "
        "SELECT c2, (SELECT c2 FROM dm.rev LIMIT 1) AS z FROM dm.sales_daily"
    )
    assert attributed(sql) == ["dm.sales_daily.internal_cost_raw"]
    assert unattributed(sql) == ["internal_cost_raw"]
    enclosed = {ref.name: ref for ref in ambiguous(sql)}
    assert enclosed["internal_cost_raw"].enclosed is True
    assert [c.name for c in enclosed["internal_cost_raw"].candidates] == ["dm.rev"]


def test_a_scalar_subquery_keeps_enclosed_columns_and_drops_a_visible_with_alias() -> None:
    """The enclosed rule is not this slice's to change; only the alias name goes."""
    sql = (
        "WITH 1 AS k SELECT region, (SELECT k + revenue FROM dm.rev LIMIT 1) AS z "
        "FROM dm.sales_daily"
    )
    assert attributed(sql) == ["dm.sales_daily.region"]
    assert unattributed(sql) == ["revenue"]
    enclosed = {ref.name: ref for ref in ambiguous(sql)}
    assert "k" not in enclosed
    assert enclosed["revenue"].enclosed is True
    assert [c.name for c in enclosed["revenue"].candidates] == ["dm.rev"]


def test_a_subquery_form_with_keeps_its_own_scope() -> None:
    """`WITH (SELECT …) AS a` grades inner columns where they are written; `a` is an alias."""
    sql = "WITH (SELECT internal_cost_raw FROM dm.sales_daily) AS c2 SELECT c2 FROM dm.stores"
    assert attributed(sql) == ["dm.sales_daily.internal_cost_raw"]
    assert unattributed(sql) == []
    assert "dm.stores" not in attributed(sql)


def test_a_select_alias_in_limit_by_is_not_a_column() -> None:
    sql = "SELECT region, sum(revenue) AS m FROM dm.sales_daily GROUP BY region LIMIT 10 BY m"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_a_qualified_name_is_not_an_alias() -> None:
    sql = "WITH 1 AS k SELECT t.k FROM dm.sales_daily AS t"
    assert attributed(sql) == ["dm.sales_daily.k"]


def test_a_lambda_parameter_is_not_an_alias_or_a_column() -> None:
    """sqlglot keeps lambda parameters as identifiers; that must stay right."""
    sql = "SELECT arrayMap(x -> x + revenue, tags) FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.revenue", "dm.sales_daily.tags"]
    assert unattributed(sql) == []
    shadowed = "WITH 1 AS r SELECT arrayMap(r -> r + 1, tags), region FROM dm.sales_daily"
    assert attributed(shadowed) == ["dm.sales_daily.region", "dm.sales_daily.tags"]
    assert unattributed(shadowed) == []


def test_an_array_join_alias_is_not_attributed() -> None:
    """ARRAY JOIN `tags AS a` reads the array, not a phantom `dm.sales_daily.a`."""
    sql = "SELECT a, sum(revenue) FROM dm.sales_daily ARRAY JOIN tags AS a GROUP BY a"
    assert attributed(sql) == [
        "dm.sales_daily.revenue",
        "dm.sales_daily.tags",
    ]


def test_a_with_alias_is_visible_in_a_union_branch() -> None:
    sql = "WITH 1 AS k SELECT k FROM dm.sales_daily UNION ALL SELECT k FROM dm.stores"
    assert attributed(sql) == []
    assert unattributed(sql) == []


def test_a_select_alias_is_not_visible_in_another_union_branch() -> None:
    sql = "SELECT region AS r FROM dm.sales_daily UNION ALL SELECT r FROM dm.stores"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.stores.r"]


# --- PostgreSQL family unchanged ------------------------------------------------------------


def test_postgres_still_reads_a_select_alias_as_a_column(tmp_path: Path) -> None:
    assert "dm.sales_daily.m" in attributed(SELECT_ALIAS_IN_SELECT, dialect="postgres")
    assert "dm.sales_daily.r" in attributed(SELECT_ALIAS_IN_WHERE, dialect="postgres")
    assert ungoverned_codes(tmp_path, SELECT_ALIAS_IN_SELECT, engine="postgres") == [
        "asset.query_ungoverned_field"
    ]
    assert "dm.sales_daily.m" in ungoverned_detail(
        tmp_path / "detail", SELECT_ALIAS_IN_SELECT, engine="postgres"
    )


def test_postgres_self_reference_still_reads_the_column() -> None:
    sql = "SELECT sum(revenue) AS revenue FROM dm.sales_daily"
    assert attributed(sql, dialect="postgres") == ["dm.sales_daily.revenue"]


# --- chart impact --------------------------------------------------------------------------


def test_a_field_read_only_through_a_with_alias_resolves_reads_field(tmp_path: Path) -> None:
    sql = "WITH revenue * 2 AS r SELECT sum(r) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = next(item for item in report.impacts if item.code == "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert "dm.sales_daily.revenue" in impact.charts[0].detail
    assert report.chart_gaps == []


def test_an_unread_subquery_with_alias_is_not_an_unattributed_column(tmp_path: Path) -> None:
    sql = (
        "WITH (SELECT dictGet('dm.rev','revenue',toUInt64(1))) AS a, (SELECT a + 1) AS b "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_live_expression_with_alias_is_not_an_unattributed_column(tmp_path: Path) -> None:
    sql = (
        "WITH dictGet('dm.rev','revenue',toUInt64(1)) AS a, (SELECT a) AS s "
        "SELECT s, sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert report.chart_gaps == []


# --- nested query in a read-through WITH (F-T-13-1) ---------------------------------------


NESTED_A_WITH = (
    "WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily) AS s "
    "SELECT region, sum(revenue) + s AS m FROM dm.sales_daily GROUP BY region"
)
NESTED_A_INLINE = (
    "SELECT region, sum(revenue) + (1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily)) "
    "AS m FROM dm.sales_daily GROUP BY region"
)
NESTED_B_WITH = (
    "WITH (SELECT max(internal_cost_raw) FROM dm.sales_daily) + 1 AS s SELECT s FROM dm.stores"
)
NESTED_B_INLINE = "SELECT (SELECT max(internal_cost_raw) FROM dm.sales_daily) + 1 FROM dm.stores"
NESTED_C_WITH = (
    "WITH region IN (SELECT region FROM dm.stores WHERE internal_cost_raw > 0) AS s "
    "SELECT region, sum(revenue) FROM dm.sales_daily WHERE s GROUP BY region"
)
NESTED_C_INLINE = (
    "SELECT region, sum(revenue) FROM dm.sales_daily "
    "WHERE region IN (SELECT region FROM dm.stores WHERE internal_cost_raw > 0) GROUP BY region"
)
NESTED_D_WITH = (
    "WITH exists(SELECT 1 FROM dm.stores WHERE internal_cost_raw > 0) AS s "
    "SELECT s FROM dm.sales_daily"
)
NESTED_D_INLINE = (
    "SELECT exists(SELECT 1 FROM dm.stores WHERE internal_cost_raw > 0) FROM dm.sales_daily"
)


NESTED_QUERY_SHAPES = [
    pytest.param(NESTED_A_WITH, NESTED_A_INLINE, id="a-scalar-add"),
    pytest.param(NESTED_B_WITH, NESTED_B_INLINE, id="b-subquery-plus"),
    pytest.param(NESTED_C_WITH, NESTED_C_INLINE, id="c-in-subquery"),
    pytest.param(NESTED_D_WITH, NESTED_D_INLINE, id="d-exists"),
    pytest.param(
        "WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily UNION ALL "
        "SELECT max(revenue) FROM dm.stores) AS s SELECT s FROM dm.rev",
        "SELECT 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily UNION ALL "
        "SELECT max(revenue) FROM dm.stores) FROM dm.rev",
        id="union",
    ),
    pytest.param(
        "WITH named AS (SELECT internal_cost_raw AS x FROM dm.sales_daily), "
        "1 + (SELECT max(x) FROM named) AS s SELECT s FROM dm.stores",
        "WITH named AS (SELECT internal_cost_raw AS x FROM dm.sales_daily) "
        "SELECT 1 + (SELECT max(x) FROM named) FROM dm.stores",
        id="outer-named-cte",
    ),
    pytest.param(
        "WITH 1 + (WITH k AS (SELECT internal_cost_raw FROM dm.sales_daily) "
        "SELECT max(internal_cost_raw) FROM k) AS s SELECT s FROM dm.stores",
        "SELECT 1 + (WITH k AS (SELECT internal_cost_raw FROM dm.sales_daily) "
        "SELECT max(internal_cost_raw) FROM k) FROM dm.stores",
        id="inner-with",
    ),
    pytest.param(
        "WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily) AS s0, "
        "s0 * 2 AS s SELECT s FROM dm.stores",
        "SELECT (1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily)) * 2 FROM dm.stores",
        id="alias-chain",
    ),
    pytest.param(
        "WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily) AS s "
        "SELECT (SELECT s FROM dm.rev LIMIT 1) FROM dm.stores",
        "SELECT (SELECT 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily) "
        "FROM dm.rev LIMIT 1) FROM dm.stores",
        id="referenced-from-scalar-subquery",
    ),
]


@pytest.mark.parametrize("with_sql,inline_sql", NESTED_QUERY_SHAPES)
def test_a_nested_query_in_a_read_through_with_matches_the_inline_form(
    tmp_path: Path, with_sql: str, inline_sql: str
) -> None:
    assert_with_matches_inline(with_sql, inline_sql)
    assert ungoverned_pairs(tmp_path / "with", with_sql) == ungoverned_pairs(
        tmp_path / "inline", inline_sql
    )


def test_a_nested_query_in_a_referenced_with_names_the_undeclared_field(
    tmp_path: Path,
) -> None:
    """Before this pass the WITH form was a clean pass; the inline form was advisory."""
    assert ungoverned_pairs(tmp_path, NESTED_A_WITH) == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    detail = ungoverned_detail(tmp_path / "detail", NESTED_A_WITH)
    assert "internal_cost_raw" in detail
    assert " s " not in f" {detail} "


def test_a_nested_query_in_a_referenced_with_is_a_chart_sql_partial(tmp_path: Path) -> None:
    _, report = field_change(tmp_path, NESTED_A_WITH, "dm.sales_daily.internal_cost_raw")
    assert charts(report) == []
    assert [gap.code for gap in report.chart_gaps] == ["chart.sql_partial"]
    assert "internal_cost_raw is not attributed to a table" in report.chart_gaps[0].detail


def test_an_unreferenced_with_with_a_nested_query_reads_nothing(tmp_path: Path) -> None:
    sql = (
        "WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily) AS s SELECT id FROM dm.stores"
    )
    assert attributed(sql) == ["dm.stores.id"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []
    assert "internal_cost_raw" not in ungoverned_detail(tmp_path / "detail", sql)


def test_a_subquery_form_with_nested_query_is_unchanged(tmp_path: Path) -> None:
    """`WITH (SELECT …) AS s` keeps its CTE scope; it is not the expression-form hole."""
    sql = "WITH (SELECT max(internal_cost_raw) FROM dm.sales_daily) AS s SELECT s FROM dm.stores"
    assert attributed(sql) == ["dm.sales_daily.internal_cost_raw"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.internal_cost_raw" in ungoverned_detail(tmp_path / "detail", sql)


def test_a_cyclic_select_alias_pair_is_read_as_columns() -> None:
    """ClickHouse rejects cyclic SELECT aliases; keep HEAD's column reading."""
    sql = "SELECT revenue * 2 AS r, r + 1 AS revenue FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.r", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []
    sql = "SELECT a + 1 AS b, b + 1 AS a FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.a", "dm.sales_daily.b"]
    assert unattributed(sql) == []


def test_a_long_select_alias_chain_is_linear_and_reads_the_source_column() -> None:
    """Each alias names the previous one twice; N=60 must stay linear, not 2^N."""
    n = 60
    parts = ["revenue AS a0"]
    parts.extend(f"a{i - 1} + a{i - 1} AS a{i}" for i in range(1, n + 1))
    sql = f"SELECT {', '.join(parts)} FROM dm.sales_daily"
    started = perf_counter()
    reads = extract_column_refs(sql, dialect="clickhouse")
    elapsed = perf_counter() - started
    assert elapsed < 2.0, elapsed
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.revenue"
    ]
    assert reads.unattributed == frozenset()


def test_a_select_alias_self_reference_beside_another_alias_still_reads_the_column() -> None:
    sql = "SELECT sum(revenue) AS revenue, revenue * 2 AS d FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_an_acyclic_alias_that_reads_into_a_cycle_keeps_cycle_members_as_columns() -> None:
    sql = "SELECT revenue * 2 AS r, r + 1 AS revenue, r AS z FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.r", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


# --- read-through state: identity, memo, caps, WITH cycles (repair pass 1 note 3) ----------


def _unread_alias(name: str) -> AmbiguousRef:
    return AmbiguousRef(
        name=name, quoted=False, candidates=(), all_tables=False, qualifier="", enclosed=False
    )


def _timed_reads(sql: str) -> tuple[ColumnReads, float]:
    started = perf_counter()
    reads = extract_column_refs(sql, dialect="clickhouse")
    return reads, perf_counter() - started


def _with_linear_chain(n: int) -> str:
    parts = ["revenue AS a0"]
    parts.extend(f"a{i - 1} + 1 AS a{i}" for i in range(1, n + 1))
    return f"WITH {', '.join(parts)} SELECT a{n} FROM dm.sales_daily"


def _with_doubling_chain(n: int) -> str:
    parts = ["revenue AS a0"]
    parts.extend(f"a{i - 1} + a{i - 1} AS a{i}" for i in range(1, n + 1))
    return f"WITH {', '.join(parts)} SELECT a{n} FROM dm.sales_daily"


def _select_forward_chain(n: int) -> str:
    parts = [f"a{i + 1} + a{i + 1} AS a{i}" for i in range(n)]
    parts.append(f"revenue AS a{n}")
    return f"SELECT {', '.join(parts)} FROM dm.sales_daily"


def _nested_query_chain_with(n: int) -> str:
    parts = ["revenue AS a0"]
    parts.extend(f"1 + (SELECT a{i - 1}) AS a{i}" for i in range(1, n + 1))
    return f"WITH {', '.join(parts)} SELECT a{n} FROM dm.sales_daily"


def _nested_query_chain_inline(n: int) -> str:
    expr = "revenue"
    for _ in range(n):
        expr = f"1 + (SELECT {expr})"
    return f"SELECT {expr} FROM dm.sales_daily"


def _nested_query_doubling_chain(n: int) -> str:
    parts = ["revenue AS a0"]
    parts.extend(f"(SELECT a{i - 1}) + (SELECT a{i - 1}) AS a{i}" for i in range(1, n + 1))
    return f"WITH {', '.join(parts)} SELECT a{n} FROM dm.sales_daily"


def _capped_alias_name(n: int) -> str:
    return f"a{n - CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH}"


def test_a_with_doubling_chain_is_linear_and_reads_the_source_column() -> None:
    reads, elapsed = _timed_reads(_with_doubling_chain(60))
    assert elapsed < 2.0, elapsed
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.revenue"
    ]
    assert reads.unattributed == frozenset()


def test_a_select_forward_chain_is_iterative_and_reads_the_source_column() -> None:
    reads, elapsed = _timed_reads(_select_forward_chain(3000))
    assert elapsed < 2.0, elapsed
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.revenue"
    ]
    assert reads.unattributed == frozenset()


def test_a_with_linear_chain_below_the_depth_cap_reads_the_source_column() -> None:
    reads, elapsed = _timed_reads(_with_linear_chain(40))
    assert elapsed < 2.0, elapsed
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.revenue"
    ]
    assert reads.unattributed == frozenset()


def test_a_with_linear_chain_past_the_depth_cap_is_unattributed_under_its_name() -> None:
    n = 3000
    reads, elapsed = _timed_reads(_with_linear_chain(n))
    assert elapsed < 2.0, elapsed
    name = _capped_alias_name(n)
    assert reads.attributed == frozenset()
    assert reads.unattributed == frozenset({name})
    assert reads.ambiguous == frozenset({_unread_alias(name)})


def test_a_nested_query_chain_below_the_depth_cap_matches_the_inline_form() -> None:
    # sqlglot's recursive parser overflows pytest's stack at ~40 nested SELECT
    # parentheses; N=20 is below the read-through cap and still parses. N=40
    # WITH is the same expansion, pinned to those reads.
    n = 20
    with_reads, inline_reads = assert_with_matches_inline(
        _nested_query_chain_with(n), _nested_query_chain_inline(n)
    )
    reads, elapsed = _timed_reads(_nested_query_chain_with(40))
    assert elapsed < 2.0, elapsed
    assert reads.attributed == with_reads.attributed == inline_reads.attributed
    assert reads.ambiguous == with_reads.ambiguous == inline_reads.ambiguous


def test_a_nested_query_chain_past_the_depth_cap_is_unattributed_under_its_name() -> None:
    n = 300
    reads, elapsed = _timed_reads(_nested_query_chain_with(n))
    assert elapsed < 2.0, elapsed
    name = _capped_alias_name(n)
    assert reads.attributed == frozenset()
    assert reads.unattributed == frozenset({name})
    assert reads.ambiguous == frozenset({_unread_alias(name)})


def test_a_nested_query_doubling_chain_hits_the_expansion_budget() -> None:
    n = 40
    assert 2 ** (n + 1) - 1 > CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET
    reads, elapsed = _timed_reads(_nested_query_doubling_chain(n))
    assert elapsed < 2.0, elapsed
    assert reads.attributed == frozenset()
    budget_names = {
        "a1",
        "a2",
        "a7",
        "a10",
        "a11",
        *(f"a{i}" for i in range(13, n)),
    }
    assert reads.unattributed == budget_names | {"revenue"}
    by_name = {ref.name: ref for ref in reads.ambiguous}
    assert by_name["revenue"] == AmbiguousRef(
        name="revenue", quoted=False, candidates=(), all_tables=True, qualifier="", enclosed=True
    )
    assert {name: by_name[name] for name in budget_names} == {
        name: _unread_alias(name) for name in budget_names
    }


def test_an_inner_with_alias_of_the_same_name_does_not_hide_the_outer_read(
    tmp_path: Path,
) -> None:
    with_sql = (
        "WITH internal_cost_raw * 2 AS x SELECT region, "
        "(WITH x + 1 AS x SELECT x FROM dm.sales_daily) AS v FROM dm.sales_daily"
    )
    inline_sql = (
        "SELECT region, (SELECT (internal_cost_raw * 2) + 1 FROM dm.sales_daily) AS v "
        "FROM dm.sales_daily"
    )
    assert_with_matches_inline(with_sql, inline_sql)
    assert ungoverned_pairs(tmp_path / "with", with_sql) == ungoverned_pairs(
        tmp_path / "inline", inline_sql
    )


def test_an_inner_with_alias_of_the_same_name_without_from_matches_inline(
    tmp_path: Path,
) -> None:
    with_sql = (
        "WITH internal_cost_raw * 2 AS x SELECT region, "
        "(WITH x + 1 AS x SELECT x) AS v FROM dm.sales_daily"
    )
    inline_sql = "SELECT region, (SELECT (internal_cost_raw * 2) + 1) AS v FROM dm.sales_daily"
    assert_with_matches_inline(with_sql, inline_sql)
    assert ungoverned_pairs(tmp_path / "with", with_sql) == ungoverned_pairs(
        tmp_path / "inline", inline_sql
    )


def test_a_cyclic_with_alias_pair_is_read_as_columns() -> None:
    """ClickHouse rejects cyclic WITH aliases; keep HEAD's column reading."""
    sql = "WITH b + 1 AS a, a + 1 AS b SELECT a FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.a"]
    assert unattributed(sql) == []
    sql = "WITH b + 1 AS a, a + 1 AS b SELECT a, b FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.a", "dm.sales_daily.b"]
    assert unattributed(sql) == []


# --- F-T-13-8: owner of the reference is a column inside the read-through -----------------


OWNER_C2_WITH = (
    "WITH internal_cost_raw * 2 AS c2 SELECT region, sum(c2) AS internal_cost_raw "
    "FROM dm.sales_daily GROUP BY region"
)
OWNER_C2_INLINE = (
    "SELECT region, sum(internal_cost_raw * 2) AS internal_cost_raw "
    "FROM dm.sales_daily GROUP BY region"
)
OWNER_AMOUNT_WITH = (
    "WITH amount * rate AS converted SELECT region, sum(converted) AS amount "
    "FROM dm.sales_daily GROUP BY region"
)
OWNER_AMOUNT_INLINE = (
    "SELECT region, sum(amount * rate) AS amount FROM dm.sales_daily GROUP BY region"
)
OWNER_FALSE_REJECT_WITH = (
    "WITH m * 2 AS d SELECT region, sum(revenue) AS m, d FROM dm.sales_daily GROUP BY region"
)
OWNER_FALSE_REJECT_INLINE = (
    "SELECT region, sum(revenue) AS m, (m * 2) AS d FROM dm.sales_daily GROUP BY region"
)
OWNER_NESTED_WITH = (
    "WITH internal_cost_raw * 2 AS c2 SELECT (SELECT c2) AS internal_cost_raw FROM dm.sales_daily"
)
OWNER_NESTED_INLINE = (
    "SELECT (SELECT internal_cost_raw * 2) AS internal_cost_raw FROM dm.sales_daily"
)


def test_a_with_read_through_from_a_select_alias_of_the_same_name_matches_inline(
    tmp_path: Path,
) -> None:
    """Inside SELECT alias X, a WITH body read through from X still treats X as the column."""
    assert_with_matches_inline(OWNER_C2_WITH, OWNER_C2_INLINE)
    assert ungoverned_pairs(tmp_path / "with", OWNER_C2_WITH) == ungoverned_pairs(
        tmp_path / "inline", OWNER_C2_INLINE
    )
    assert ungoverned_pairs(tmp_path / "with", OWNER_C2_WITH) == [
        ("asset.query_ungoverned_field", "review")
    ]
    detail = ungoverned_detail(tmp_path / "detail", OWNER_C2_WITH)
    assert "dm.sales_daily.internal_cost_raw" in detail
    assert "c2" not in detail
    _, report = field_change(tmp_path / "impact", OWNER_C2_WITH, "dm.sales_daily.internal_cost_raw")
    assert charts(report) == [
        (
            "operator_table",
            ChartRelation.READS_FIELD,
            "query reads dm.sales_daily.internal_cost_raw",
        )
    ]
    assert report.chart_gaps == []


def test_a_converted_amount_kept_under_the_same_name_matches_inline(tmp_path: Path) -> None:
    assert_with_matches_inline(OWNER_AMOUNT_WITH, OWNER_AMOUNT_INLINE)
    assert ungoverned_pairs(tmp_path / "with", OWNER_AMOUNT_WITH) == ungoverned_pairs(
        tmp_path / "inline", OWNER_AMOUNT_INLINE
    )
    detail = ungoverned_detail(tmp_path / "detail", OWNER_AMOUNT_WITH)
    assert "dm.sales_daily.amount" in detail
    assert "converted" not in detail
    _, report = field_change(tmp_path / "impact", OWNER_AMOUNT_WITH, "dm.sales_daily.amount")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.amount")
    ]
    assert report.chart_gaps == []


def test_a_with_alias_without_an_owner_does_not_read_a_select_alias_of_the_same_name(
    tmp_path: Path,
) -> None:
    """FALSE-REJECT CONTROL: `d` has no owner, so `m` in its body stays the SELECT alias."""
    assert_with_matches_inline(OWNER_FALSE_REJECT_WITH, OWNER_FALSE_REJECT_INLINE)
    assert attributed(OWNER_FALSE_REJECT_WITH) == [
        "dm.sales_daily.region",
        "dm.sales_daily.revenue",
    ]
    assert "dm.sales_daily.m" not in attributed(OWNER_FALSE_REJECT_WITH)
    assert unattributed(OWNER_FALSE_REJECT_WITH) == []
    assert ungoverned_codes(tmp_path, OWNER_FALSE_REJECT_WITH) == []


def test_an_outer_select_alias_is_not_the_owner_inside_a_nested_query() -> None:
    """SELECT aliases are not visible in subqueries; the inner owner is computed there."""
    assert_with_matches_inline(OWNER_NESTED_WITH, OWNER_NESTED_INLINE)


# --- F-T-13-10: cycles that cross SELECT and WITH definitions -----------------------------


MIXED_CYCLE_WITH = "WITH r + 1 AS w SELECT revenue * 2 AS r, w AS revenue FROM dm.sales_daily"
MIXED_CYCLE_INLINE = "SELECT revenue * 2 AS r, (r + 1) AS revenue FROM dm.sales_daily"
TWO_STEP_WITH = (
    "WITH x_undeclared * 2 AS w SELECT region, w AS r2, r2 + 1 AS x_undeclared FROM dm.sales_daily"
)
TWO_STEP_INLINE = (
    "SELECT region, (x_undeclared * 2) AS r2, r2 + 1 AS x_undeclared FROM dm.sales_daily"
)
OWNER_VIA_K_WITH = (
    "WITH internal_cost_raw * 2 AS c2 SELECT c2 AS k, sum(k) AS internal_cost_raw "
    "FROM dm.sales_daily"
)
OWNER_VIA_K_INLINE = (
    "SELECT (internal_cost_raw * 2) AS k, sum(k) AS internal_cost_raw FROM dm.sales_daily"
)


def test_a_cycle_across_select_and_with_aliases_matches_inline() -> None:
    assert_with_matches_inline(MIXED_CYCLE_WITH, MIXED_CYCLE_INLINE)
    assert attributed(MIXED_CYCLE_WITH) == ["dm.sales_daily.r", "dm.sales_daily.revenue"]
    assert unattributed(MIXED_CYCLE_WITH) == []


def test_a_two_step_select_with_cycle_matches_inline(tmp_path: Path) -> None:
    assert_with_matches_inline(TWO_STEP_WITH, TWO_STEP_INLINE)
    assert ungoverned_pairs(tmp_path / "with", TWO_STEP_WITH) == ungoverned_pairs(
        tmp_path / "inline", TWO_STEP_INLINE
    )
    _, report = field_change(tmp_path / "impact", TWO_STEP_WITH, "dm.sales_daily.x_undeclared")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.x_undeclared")
    ]
    assert report.chart_gaps == []


def test_a_select_with_cycle_through_an_intermediate_alias_matches_inline(
    tmp_path: Path,
) -> None:
    assert_with_matches_inline(OWNER_VIA_K_WITH, OWNER_VIA_K_INLINE)
    assert ungoverned_pairs(tmp_path / "with", OWNER_VIA_K_WITH) == ungoverned_pairs(
        tmp_path / "inline", OWNER_VIA_K_INLINE
    )
    _, report = field_change(
        tmp_path / "impact", OWNER_VIA_K_WITH, "dm.sales_daily.internal_cost_raw"
    )
    assert charts(report) == [
        (
            "operator_table",
            ChartRelation.READS_FIELD,
            "query reads dm.sales_daily.internal_cost_raw",
        )
    ]
    assert report.chart_gaps == []


# --- F-T-13-9: a cut expansion is not memoised --------------------------------------------


def _with_undeclared_chain(n: int, select: str) -> str:
    parts = ["x_undeclared AS a0"]
    parts.extend(f"a{i - 1} + 1 AS a{i}" for i in range(1, n + 1))
    return f"WITH {', '.join(parts)} SELECT {select} FROM dm.sales_daily"


def test_a_shallow_reference_after_a_cut_expansion_still_reads_the_source_column() -> None:
    """A truncated (alias, scope, owner) is not memoised; the later shallow walk expands."""
    deep_first = _with_undeclared_chain(70, "a70, a10")
    shallow_first = _with_undeclared_chain(70, "a10, a70")
    deep_reads = extract_column_refs(deep_first, dialect="clickhouse")
    shallow_reads = extract_column_refs(shallow_first, dialect="clickhouse")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in deep_reads.attributed) == [
        "dm.sales_daily.x_undeclared"
    ]
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in shallow_reads.attributed) == [
        "dm.sales_daily.x_undeclared"
    ]
    # Deep-first hits the cap on a70 (path a70..a7, then a6), then a10 reads the source.
    assert deep_reads.unattributed == frozenset({"a6"})
    assert deep_reads.ambiguous == frozenset({_unread_alias("a6")})
    # Shallow-first fully expands a10 (and memoises it); a70 then stops at that memo.
    assert shallow_reads.unattributed == frozenset()
    assert shallow_reads.ambiguous == frozenset()


def test_a_cut_reference_uses_the_unseen_source_advisory_wording(tmp_path: Path) -> None:
    """F-T-13-11: the cap reuses the generic unseen-source clause.

    The unexpanded definition is that source.
    """
    sql = _with_undeclared_chain(70, "a70")
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert reads.attributed == frozenset()
    assert reads.unattributed == frozenset({"a6"})
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    detail = ungoverned_detail(tmp_path / "detail", sql)
    assert "a6" in detail
    assert "could not be attributed to one table without guessing" in detail
    assert "a source this snapshot cannot see into" in detail


def test_select_aliases_over_a_long_with_chain_stay_linear() -> None:
    """300 SELECT aliases, each reading the top of a 300-long WITH chain, under 2 s."""
    n = 300
    with_parts = ["revenue AS a0"]
    with_parts.extend(f"a{i - 1} + 1 AS a{i}" for i in range(1, n))
    select_parts = [f"a{n - 1} AS s{i}" for i in range(n)]
    sql = f"WITH {', '.join(with_parts)} SELECT {', '.join(select_parts)} FROM dm.sales_daily"
    _reads, elapsed = _timed_reads(sql)
    assert elapsed < 2.0, elapsed
