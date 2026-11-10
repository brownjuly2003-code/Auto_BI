"""CTE exemptions are limited to the query that declares each CTE."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import SQLGuardError, TableRef, extract_table_refs, guard_sql
from auto_bi.auth import forbidden_tables
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, Viz
from tests.test_sql_guard_clickhouse_alias import ungoverned_detail, ungoverned_pairs


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT count(*) FROM a WHERE EXISTS " "(WITH a AS (SELECT 1) SELECT 1 FROM a)",
            {TableRef("a", False)},
        ),
        (
            'SELECT count(*) FROM "a" WHERE EXISTS ' '(WITH "a" AS (SELECT 1) SELECT 1 FROM "a")',
            {TableRef("a", True)},
        ),
        (
            "SELECT count(*) FROM a UNION ALL " "(WITH a AS (SELECT 1) SELECT count(*) FROM a)",
            {TableRef("a", False)},
        ),
        (
            "SELECT count(*) FROM (SELECT 1 FROM a) AS physical "
            "CROSS JOIN (WITH a AS (SELECT 1) SELECT 1 FROM a) AS named",
            {TableRef("a", False)},
        ),
        (
            "WITH a AS (SELECT 1) " "SELECT count(*) FROM (SELECT 1 FROM a) AS nested",
            set(),
        ),
        (
            "WITH a AS (SELECT 1) " "SELECT count(*) FROM a UNION ALL SELECT count(*) FROM a",
            set(),
        ),
        (
            "WITH unused AS (SELECT count(*) FROM a) SELECT 1",
            {TableRef("a", False)},
        ),
        (
            "WITH unused AS (WITH a AS (SELECT 1) SELECT 1 FROM a) " "SELECT count(*) FROM a",
            {TableRef("a", False)},
        ),
    ],
    ids=[
        "descendant-cte",
        "quoted-descendant-cte",
        "union-sibling",
        "derived-sibling",
        "inherited-outer-cte",
        "root-cte-union",
        "unread-cte-physical-table",
        "unread-nested-cte",
    ],
)
def test_cte_visibility_is_scoped_to_declaring_query(
    sql: str, dialect: str, expected: set[TableRef]
) -> None:
    assert extract_table_refs(sql, dialect=dialect) == expected


def test_clickhouse_in_rewrite_keeps_outer_physical_table_visible() -> None:
    sql = (
        "SELECT count(*) FROM dm.sales_daily WHERE region IN a AND EXISTS "
        "(WITH a AS (SELECT 1) SELECT 1 FROM a)"
    )
    assert extract_table_refs(sql, dialect="clickhouse") == {
        TableRef("a", False),
        TableRef("dm.sales_daily", False),
    }


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize(
    ("sql", "rejects"),
    [
        (
            "WITH dictionary AS (SELECT 1) SELECT count(*) FROM dictionary",
            False,
        ),
        (
            "SELECT count(*) FROM dictionary WHERE EXISTS "
            "(WITH dictionary AS (SELECT 1) SELECT 1 FROM dictionary)",
            True,
        ),
        (
            "SELECT count(*) FROM dictionary UNION ALL "
            "(WITH dictionary AS (SELECT 1) SELECT count(*) FROM dictionary)",
            True,
        ),
        (
            "WITH dictionary AS (SELECT 1) "
            "SELECT count(*) FROM (SELECT 1 FROM dictionary) AS nested",
            False,
        ),
        (
            "WITH dictionary AS (SELECT 1) SELECT count(*) FROM dictionary "
            "UNION ALL SELECT count(*) FROM dictionary",
            False,
        ),
    ],
    ids=[
        "local-cte-control",
        "outer-denylist-visible",
        "union-sibling-denylist",
        "inherited-outer-cte",
        "root-cte-union",
    ],
)
def test_nested_dictionary_cte_does_not_hide_outer_denylist(
    sql: str, dialect: str, rejects: bool
) -> None:
    if rejects:
        with pytest.raises(SQLGuardError, match=r"forbidden table function in SQL: dictionary\(\)"):
            guard_sql(sql, dialect=dialect)
    else:
        guard_sql(sql, dialect=dialect)


@pytest.mark.parametrize("engine", ["clickhouse", "postgres"])
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT count(*) FROM a WHERE EXISTS " "(WITH a AS (SELECT 1) SELECT 1 FROM a)",
            [("asset.query_ungoverned", "review")],
        ),
        (
            "WITH a AS (SELECT 1) " "SELECT count(*) FROM (SELECT 1 FROM a) AS nested",
            [],
        ),
    ],
    ids=["descendant-cte-is-physical", "inherited-cte-control"],
)
def test_governance_reports_outer_physical_read(
    tmp_path: Path, sql: str, engine: str, expected: list[tuple[str, str]]
) -> None:
    assert ungoverned_pairs(tmp_path, sql, engine=engine) == expected
    if expected:
        assert "reads a," in ungoverned_detail(tmp_path / "detail", sql, engine=engine)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT count(*) FROM a WHERE EXISTS (WITH a AS (SELECT 1) SELECT 1 FROM a)",
            ["a"],
        ),
        (
            "SELECT count(*) FROM a UNION ALL (WITH a AS (SELECT 1) SELECT count(*) FROM a)",
            ["a"],
        ),
        (
            "WITH a AS (SELECT 1) SELECT count(*) FROM (SELECT 1 FROM a) AS nested",
            [],
        ),
    ],
    ids=["descendant-cte-is-forbidden", "sibling-cte-is-forbidden", "inherited-cte-control"],
)
def test_raw_sql_rbac_reports_outer_physical_read(sql: str, expected: list[str]) -> None:
    spec = DashboardSpec(
        title="raw",
        charts=[
            ChartSpec(
                id="raw",
                title="raw",
                viz=Viz.TABLE,
                query=ChartQuery(table="dm.sales_daily", raw_sql=sql),
            )
        ],
    )
    assert forbidden_tables(spec, ["dm"]) == expected
