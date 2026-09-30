"""ClickHouse reachability distinguishes differently cased WITH declarations."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import clickhouse_has_reachable_star, extract_column_refs
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact_dictionary import charts, field_change
from tests.test_sql_guard_clickhouse_alias import ungoverned_detail

STAR_CASES = [
    (
        "WITH tuple(*) AS A, 1 AS a SELECT a FROM dm.sales_daily",
        False,
    ),
    (
        'WITH tuple(*) AS "A", 1 AS "a" SELECT "a" FROM dm.sales_daily',
        False,
    ),
    (
        "WITH tuple(*) AS A, 1 AS a SELECT A FROM dm.sales_daily",
        True,
    ),
    (
        'WITH tuple(*) AS "A", 1 AS a SELECT A FROM dm.sales_daily',
        True,
    ),
    (
        "WITH tuple(*) AS A, 1 AS a SELECT A.1 FROM dm.sales_daily",
        True,
    ),
    (
        "WITH A AS (SELECT * FROM dm.sales_daily) "
        "SELECT (WITH a AS (SELECT 999 AS revenue) SELECT sum(revenue) FROM A)",
        True,
    ),
    (
        "WITH a AS (SELECT * FROM dm.sales_daily) "
        "SELECT (WITH A AS (SELECT 999 AS revenue) SELECT sum(revenue) FROM a)",
        True,
    ),
    (
        'WITH "A" AS (SELECT * FROM dm.sales_daily) '
        'SELECT (WITH "a" AS (SELECT 999 AS revenue) SELECT sum(revenue) FROM "A")',
        True,
    ),
    (
        "WITH A AS (SELECT * FROM dm.sales_daily) "
        "SELECT (WITH A AS (SELECT 999 AS revenue) SELECT sum(revenue) FROM A)",
        False,
    ),
    (
        "WITH A AS (SELECT * FROM dm.sales_daily), a AS (SELECT 999 AS revenue) "
        "SELECT revenue FROM a",
        False,
    ),
    (
        "WITH A AS (SELECT * FROM dm.sales_daily) SELECT revenue FROM A",
        True,
    ),
    (
        "WITH tuple(*) AS A SELECT A.revenue FROM dm.sales_daily AS A",
        False,
    ),
]
STAR_CASE_IDS = [
    "scalar-different-case-unread",
    "scalar-quoted-different-case-unread",
    "scalar-uppercase-read",
    "scalar-quoted-declaration-bare-reference",
    "scalar-uppercase-tuple-access",
    "named-different-case-inner",
    "named-reversed-case-inner",
    "named-quoted-different-case-inner",
    "named-exact-inner-shadow",
    "named-different-case-sibling-unread",
    "named-uppercase-read",
    "exact-table-alias-precedes-scalar",
]


@pytest.mark.parametrize("sql,starred", STAR_CASES, ids=STAR_CASE_IDS)
def test_governance_star_reachability_preserves_clickhouse_case(
    tmp_path: Path, sql: str, starred: bool
) -> None:
    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ("it selects *" in ungoverned_detail(tmp_path, sql)) is starred
    assert clickhouse_has_reachable_star(sql) is starred


@pytest.mark.parametrize("sql,starred", STAR_CASES, ids=STAR_CASE_IDS)
def test_chart_star_gap_preserves_clickhouse_case(tmp_path: Path, sql: str, starred: bool) -> None:
    _, report = field_change(tmp_path, sql, "dm.sales_daily.revenue")
    star_gap = any(
        gap.code == "chart.sql_partial" and "it selects *" in gap.detail
        for gap in report.chart_gaps
    )
    assert star_gap is starred


@pytest.mark.parametrize(
    "outer,inner,reads",
    [("A", "a", True), ("a", "A", True), ('"A"', '"a"', True), ("A", "A", False)],
    ids=["different-case", "reversed-case", "quoted-different-case", "exact-shadow"],
)
def test_named_cte_in_table_keeps_the_correct_dictionary_read(
    tmp_path: Path, outer: str, inner: str, reads: bool
) -> None:
    sql = (
        f"WITH {outer} AS (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) AS v), "
        f"wrapper AS (WITH {inner} AS (SELECT 999 AS v) "
        f"SELECT store_id FROM dm.sales_daily WHERE store_id IN {outer}) "
        "SELECT sum(store_id) FROM wrapper"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    expected = (
        [("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")]
        if reads
        else []
    )
    assert charts(report) == expected
